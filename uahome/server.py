"""Local-first HTTP application. No external purchases or messages are sent."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .domain import (ValidationError, build_listing, build_purchase,
                     calculate_margin, draft_review_reply)
from .storage import ConflictError, MissingError, Store
from .sourcing import calculate_import_batch

STATIC = Path(__file__).parent / "static"
MAX_BODY = 12 * 1024 * 1024


def safe_csv_cell(value):
    """Prevent spreadsheet formula execution in user-controlled exports."""
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")):
        return "'" + text
    return text


def products_csv(products, listings=False):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if listings:
        writer.writerow(["상품ID", "판매처", "상품명_초안", "설명_초안", "키워드", "판매가", "확인필요"])
        for product in products:
            draft = build_listing(product)
            writer.writerow([safe_csv_cell(v) for v in [
                product["id"], draft.get("channel", ""), draft["title"], draft["description"],
                ", ".join(draft.get("keywords", [])), product["costs"]["sale_price"],
                ", ".join(draft.get("missing_fields", []))]])
    else:
        writer.writerow(["상품ID", "상품명", "공급처", "판매처", "상태", "판매가", "개당예상이익", "마진율", "수량", "수동평가점수"])
        for product in products:
            writer.writerow([safe_csv_cell(v) for v in [
                product["id"], product["name"], product.get("supplier", ""), product.get("channel", ""),
                product["status"], product["costs"]["sale_price"], product["margin"]["profit"],
                product["margin"]["margin_rate"], product["quantity"], product["ranking"]["score"]]])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def prepare_work(store):
    """Deterministically prepare drafts/approval requests, never approve or execute."""
    state = store.get_state()
    prepared, skipped = [], []
    for review in state["reviews"]:
        if review["status"] != "pending" or review.get("reply_draft", "").strip():
            continue
        try:
            product = next(p for p in state["products"] if p["id"] == review["product_id"])
            draft = draft_review_reply(review, product)
            store.update_review(review["id"], {"reply_draft": draft["text"]}, review["revision"])
            prepared.append({"kind": "review_draft", "id": review["id"]})
        except (ValidationError, ConflictError, MissingError) as error:
            skipped.append({"id": review["id"], "reason": str(error)})
    for product in state["products"]:
        kind = "purchase" if product["status"] == "selected" else (
            "listing" if product["status"] in ("received", "listing_ready") else None)
        if not kind:
            continue
        try:
            approval = store.request_approval(kind, product["id"])
            prepared.append({"kind": kind, "id": approval["id"]})
        except (ValidationError, ConflictError, MissingError) as error:
            skipped.append({"id": product["id"], "reason": str(error)})
    return {"prepared": prepared, "skipped": skipped}


def add_demo(store):
    """Only called by explicit user action. Preserve current records."""
    state = store.get_state()
    marker = "UAHOME-DEMO-001"
    if any(marker in p.get("notes", "") for p in state["products"]):
        return {"created": False, "message": "예시 데이터가 이미 있습니다."}
    product = store.create_product({
        "name": "접이식 수납 바스켓 (예시)", "category": "생활·수납", "supplier": "1688 예시 공급처",
        "channel": "쿠팡 로켓그로스", "source_url": "https://example.com/sample", "notes": marker,
        "quantity": 20, "moq": 10, "lead_days": 7, "status": "candidate",
        "demand_score": 4, "competition_score": 2, "supplier_score": 3,
        "keywords": "수납, 바스켓, 정리", "selling_points": "접어서 보관 가능\n손잡이 포함",
        "specs": "예시 정보: 가로 30cm · 세로 20cm · 폴리에스터 · 베이지",
        "costs": {"sale_price": 19900, "purchase_price": 32.5, "exchange_rate": 200,
                  "inbound_shipping": 400, "outbound_shipping": 0, "packaging": 500,
                  "fulfillment_fee": 3000, "storage_fee": 100,
                  "other_cost": 0, "customer_shipping": 0, "fixed_fee": 0,
                  "platform_fee_rate": 6, "payment_fee_rate": 2, "ad_rate": 5,
                  "return_rate": 3, "return_cost": 6000, "tax_reserve_rate": 2,
                  "target_margin_rate": 25}})
    store.create_review({"product_id": product["id"], "rating": 4,
                         "content": "[예시 리뷰] 정리하기 편해요. 배송은 조금 늦었어요.",
                         "customer_name": "예시 고객", "channel": "예시", "external_id": "demo-review-001"})
    return {"created": True, "message": "실제 판매 정보가 아닌 예시 상품과 리뷰를 추가했습니다."}


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store):
        super().__init__(address, Handler)
        self.store = store


class Handler(BaseHTTPRequestHandler):
    server_version = "UaHome/0.1"

    def log_message(self, fmt, *args):
        # Never log query strings, request payloads, customer content or credentials.
        sys.stderr.write(f"uahome {self.command} {getattr(self, 'response_code', '-')}\n")

    def respond(self, code, body, content_type="application/json; charset=utf-8", filename=None):
        self.response_code = code
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def json_body(self):
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ValidationError("JSON 형식으로 요청해 주세요.")
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            raise ValidationError("요청 크기가 올바르지 않습니다.")
        if not 0 < length <= MAX_BODY:
            raise ValidationError("요청은 12MB 이하의 비어 있지 않은 JSON이어야 합니다.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise ValidationError("JSON 내용을 확인해 주세요.")
        if not isinstance(data, dict):
            raise ValidationError("요청은 JSON 객체여야 합니다.")
        return data

    def check_origin(self):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            hostname = parsed.hostname
            if hostname not in ("localhost", "127.0.0.1", "::1") or parsed.port != self.server.server_port:
                raise ValidationError("로컬 주소로 접속해 주세요.")
        except ValueError:
            raise ValidationError("접속 주소가 올바르지 않습니다.")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise PermissionError("다른 사이트에서 보낸 요청은 허용하지 않습니다.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError("다른 사이트에서 보낸 요청은 허용하지 않습니다.")

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        try:
            self.check_origin()
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self.api(method, path)
            elif method == "GET":
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/styles.css": ("styles.css", "text/css; charset=utf-8")}
                if path not in assets:
                    raise MissingError("페이지를 찾을 수 없습니다.")
                name, mime = assets[path]
                self.respond(200, (STATIC / name).read_bytes(), mime)
            else:
                raise MissingError("요청 경로를 찾을 수 없습니다.")
        except MissingError as error:
            self.respond(404, {"error": str(error)})
        except ConflictError as error:
            self.respond(409, {"error": str(error)})
        except PermissionError as error:
            self.respond(403, {"error": str(error)})
        except (ValidationError, TypeError, KeyError) as error:
            self.respond(400, {"error": str(error) if isinstance(error, ValidationError) else "필수 입력값과 형식을 확인해 주세요."})
        except Exception:
            # Keep implementation errors out of customer-facing JSON and logs.
            self.respond(500, {"error": "처리 중 오류가 발생했습니다. 입력을 확인한 뒤 다시 시도해 주세요."})

    def api(self, method, path):
        store = self.server.store
        if method == "GET":
            if path == "/api/state":
                return self.respond(200, {"data": store.get_state()})
            if path == "/api/backup":
                return self.respond(200, {"data": store.export_backup()}, filename="uahome-backup.json")
            if path in ("/api/export/products.csv", "/api/export/listings.csv"):
                return self.respond(200, products_csv(store.get_state()["products"], "listings" in path),
                                    "text/csv; charset=utf-8", "uahome-listings.csv" if "listings" in path else "uahome-products.csv")
            raise MissingError("요청 경로를 찾을 수 없습니다.")
        data = self.json_body()
        result = None
        if method == "POST" and path == "/api/products":
            result = store.create_product(data)
        elif method == "POST" and path == "/api/reviews":
            result = store.create_review(data)
        elif method == "POST" and path == "/api/calculate":
            costs = data.get("costs")
            if not isinstance(costs, dict):
                raise ValidationError("비용 정보를 입력해 주세요.")
            result = calculate_margin(costs)
        elif method == "POST" and path == "/api/import/calculate":
            result = calculate_import_batch(data)
        elif method == "POST" and path == "/api/approvals":
            result = store.request_approval(data.get("kind"), data.get("product_id"), data.get("review_id"))
        elif method == "POST" and path == "/api/import":
            backup = data.get("backup")
            # GET /api/backup uses the normal API envelope; accept that exact
            # downloaded file as well as a raw schema-versioned backup.
            if isinstance(backup, dict) and set(backup) == {"data"}:
                backup = backup["data"]
            result = store.import_backup(backup)
        elif method == "POST" and path == "/api/demo":
            result = add_demo(store)
        elif method == "POST" and path == "/api/assist":
            result = prepare_work(store)
        else:
            match = re.fullmatch(r"/api/(products|reviews|approvals)/([a-zA-Z0-9_-]+)(?:/(listing|purchase|draft|decision))?", path)
            if not match:
                raise MissingError("요청 경로를 찾을 수 없습니다.")
            entity, item_id, action = match.groups()
            if entity == "products" and not action and method == "PATCH":
                revision = data.pop("revision", None)
                result = store.update_product(item_id, data, revision)
            elif entity == "products" and not action and method == "DELETE":
                store.delete_product(item_id, data.get("revision"))
                result = {"deleted": True}
            elif entity == "reviews" and not action and method == "PATCH":
                revision = data.pop("revision", None)
                result = store.update_review(item_id, data, revision)
            elif entity == "approvals" and action == "decision" and method == "POST":
                result = store.decide_approval(item_id, data.get("decision"), data.get("revision"))
            elif method == "POST" and entity == "products" and action in ("listing", "purchase"):
                product = next((p for p in store.get_state()["products"] if p["id"] == item_id), None)
                if not product:
                    raise MissingError("상품을 찾을 수 없습니다.")
                result = build_listing(product) if action == "listing" else build_purchase(product)
            elif method == "POST" and entity == "reviews" and action == "draft":
                state = store.get_state()
                review = next((r for r in state["reviews"] if r["id"] == item_id), None)
                if not review:
                    raise MissingError("리뷰를 찾을 수 없습니다.")
                product = next(p for p in state["products"] if p["id"] == review["product_id"])
                result = draft_review_reply(review, product)
            else:
                raise MissingError("요청 경로와 메서드를 확인해 주세요.")
        self.respond(200, {"data": result})


def main():
    parser = argparse.ArgumentParser(description="우아홈 반자동 운영 도구 (내 컴퓨터 전용)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default=os.environ.get("UAHOME_DATABASE", "data/uahome.sqlite3"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535여야 합니다.")
    store = Store(args.database)
    server = AppServer(("127.0.0.1", args.port), store)
    print(f"우아홈 실행: http://127.0.0.1:{args.port}", flush=True)
    print("데이터는 내 컴퓨터에 저장됩니다. 종료: Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
