"""Transactional local storage; approvals record human review, never API execution."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from uuid import UUID, uuid4

from .domain import (
    ValidationError, normalize_product, calculate_margin, rank_product,
    build_listing, build_purchase,
)


class MissingError(LookupError):
    pass


class ConflictError(ValueError):
    pass


PRODUCT_STATUSES = {"candidate", "selected", "purchase_approved", "ordered", "received", "listing_ready", "listed", "paused", "inbound_planned", "in_transit", "stocked"}
REVIEW_STATUSES = {"pending", "drafted", "approved", "replied"}
APPROVAL_STATUSES = {"pending", "approved", "rejected", "completed"}
KINDS = {"purchase", "listing", "review_reply"}
PRODUCT_TRANSITIONS = {
    "candidate": {"selected", "paused"},
    "selected": {"candidate", "paused"},
    "purchase_approved": {"paused"},
    "ordered": {"received", "paused"},
    "received": {"listing_ready", "paused"},
    "listing_ready": {"received", "paused"},
    "listed": {"inbound_planned", "paused"},
    "inbound_planned": {"listed", "in_transit", "paused"},
    "in_transit": {"stocked", "paused"},
    "stocked": {"paused"},
    "paused": {"candidate", "selected"},
}
CONNECTORS = [
    {"id": "sales", "label": "판매처", "status": "not_connected", "description": "연결 전 · 등록 파일과 초안을 수동으로 전달합니다."},
    {"id": "supplier", "label": "사입처", "status": "not_connected", "description": "연결 전 · 실제 발주·결제는 사입처에서 직접 진행합니다."},
    {"id": "reviews", "label": "고객 리뷰", "status": "not_connected", "description": "연결 전 · 실제 리뷰를 입력하고 답변 초안을 검토합니다."},
]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError) as exc:
        raise ValidationError("올바른 JSON 데이터가 필요합니다.") from exc


def _mapping(value, name="데이터"):
    if not isinstance(value, dict):
        raise ValidationError(f"{name}는 객체여야 합니다.")
    return value


def _text(value, name, required=False, limit=20000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValidationError(f"{name}는 {limit:,}자 이하의 문자열이어야 합니다.")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{name}을(를) 입력하세요.")
    return value


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("현재 데이터의 revision 정수가 필요합니다.")
    return value


def _id(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValidationError("올바른 UUID가 필요합니다.") from exc
    return value


def _timestamp(value):
    try:
        datetime.fromisoformat(_text(value, "기록 시각", True, 100))
    except ValueError as exc:
        raise ValidationError("기록 시각이 올바르지 않습니다.") from exc
    return value


def _review_fields(data):
    _mapping(data, "리뷰")
    rating = data.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValidationError("리뷰 별점은 1~5 정수여야 합니다.")
    status = data.get("status", "pending")
    if not isinstance(status, str) or status not in REVIEW_STATUSES:
        raise ValidationError("리뷰 상태가 올바르지 않습니다.")
    result = {
        "product_id": _id(data.get("product_id")),
        "rating": rating,
        "content": _text(data.get("content", ""), "실제 리뷰 내용", True, 50000),
        "customer_name": _text(data.get("customer_name", ""), "고객명", limit=200),
        "channel": _text(data.get("channel", ""), "리뷰 판매처", limit=200),
        "external_id": _text(data.get("external_id", ""), "외부 리뷰 ID", limit=500),
        "reply_draft": _text(data.get("reply_draft", ""), "답변 초안"),
        "status": status,
    }
    if status in {"drafted", "approved", "replied"} and not result["reply_draft"]:
        raise ValidationError("답변 초안을 먼저 작성하세요.")
    return result


class Store:
    def __init__(self, db_path):
        self._lock = threading.RLock()
        self._keepalive = None
        if str(db_path) == ":memory:":
            self.path = f"file:uahome-{uuid4()}?mode=memory&cache=shared"
            self.uri = True
            self._keepalive = self._connect()
        else:
            target = Path(db_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(target)
            self.uri = False
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id), doc TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id), review_id TEXT REFERENCES reviews(id), doc TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS reviews_product ON reviews(product_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS approvals_product ON approvals(product_id)")

    def close(self):
        if self._keepalive is not None:
            self._keepalive.close()
            self._keepalive = None

    def _connect(self):
        conn = sqlite3.connect(self.path, uri=self.uri, timeout=15)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _transaction(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _get(conn, table, ident):
        row = conn.execute(f"SELECT doc FROM {table} WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise MissingError("요청한 항목을 찾을 수 없습니다.")
        return json.loads(row["doc"])

    @staticmethod
    def _all(conn, table):
        return [json.loads(row["doc"]) for row in conn.execute(f"SELECT doc FROM {table} ORDER BY rowid")]

    @staticmethod
    def _save(conn, table, data, insert=False):
        if insert:
            if table == "reviews":
                conn.execute("INSERT INTO reviews(id,product_id,doc) VALUES (?,?,?)", (data["id"], data["product_id"], _encode(data)))
            elif table == "approvals":
                conn.execute("INSERT INTO approvals(id,product_id,review_id,doc) VALUES (?,?,?,?)", (data["id"], data["product_id"], data.get("review_id"), _encode(data)))
            else:
                conn.execute(f"INSERT INTO {table}(id,doc) VALUES (?,?)", (data["id"], _encode(data)))
        else:
            conn.execute(f"UPDATE {table} SET doc=? WHERE id=?", (_encode(data), data["id"]))

    @staticmethod
    def _match_revision(data, revision):
        if data["revision"] != _revision(revision):
            raise ConflictError("다른 작업에서 변경된 데이터입니다. 새로고침 후 다시 검토하세요.")

    def _event(self, conn, kind, ident, summary, data=None):
        event = {"id": str(uuid4()), "type": kind, "entity_id": ident, "summary": summary,
                 "created_at": _now(), "data": data or {}}
        self._save(conn, "events", event, True)

    @staticmethod
    def _bump(data):
        return {**data, "revision": data["revision"] + 1, "updated_at": _now()}

    def get_state(self):
        with self._transaction() as conn:
            products = self._all(conn, "products")
            for item in products:
                item["margin"] = calculate_margin(item["costs"])
                item["ranking"] = rank_product(item)
            return {"products": products, "reviews": self._all(conn, "reviews"),
                    "approvals": self._all(conn, "approvals"),
                    "events": self._all(conn, "events")[-300:][::-1],
                    "connectors": [dict(item) for item in CONNECTORS]}

    def create_product(self, data):
        clean = normalize_product(_mapping(data, "상품"))
        if clean["status"] not in {"candidate", "selected", "paused"}:
            raise ValidationError("새 상품은 후보·선정·보류 상태로 시작하세요.")
        now = _now()
        result = {**clean, "id": str(uuid4()), "created_at": now, "updated_at": now, "revision": 1}
        with self._transaction() as conn:
            self._save(conn, "products", result, True)
            self._event(conn, "product_created", result["id"], f"상품 후보 추가: {result['name']}")
        return result

    def update_product(self, ident, data, revision):
        _mapping(data, "상품")
        with self._transaction() as conn:
            old = self._get(conn, "products", ident)
            self._match_revision(old, revision)
            merged = {**old, **data, "costs": {**old["costs"], **_mapping(data.get("costs", {}), "비용")}}
            clean = normalize_product(merged)
            if clean["status"] != old["status"] and clean["status"] not in PRODUCT_TRANSITIONS[old["status"]]:
                raise ValidationError("해당 단계로 바로 변경할 수 없습니다. 승인함의 검토·수동 완료 절차를 진행하세요.")
            old_clean = normalize_product(old)
            if clean == old_clean:
                return old
            result = {**clean, "id": old["id"], "created_at": old["created_at"], "updated_at": _now(), "revision": old["revision"] + 1}
            self._save(conn, "products", result)
            if clean["status"] != old["status"] and clean["status"] in {"inbound_planned", "in_transit", "stocked"}:
                self._event(conn, "product_updated", ident, f"센터 입고 단계 수동 기록: {result['name']}",
                            {"execution_mode": "manual", "from_status": old["status"], "to_status": clean["status"]})
            else:
                self._event(conn, "product_updated", ident, f"상품 정보 변경: {result['name']}")
        return result

    def delete_product(self, ident, revision):
        with self._transaction() as conn:
            old = self._get(conn, "products", ident)
            self._match_revision(old, revision)
            if conn.execute("SELECT 1 FROM reviews WHERE product_id=? UNION ALL SELECT 1 FROM approvals WHERE product_id=? LIMIT 1", (ident, ident)).fetchone():
                raise ConflictError("리뷰나 승인 기록이 연결된 상품은 삭제할 수 없습니다. 보류 상태를 사용하세요.")
            conn.execute("DELETE FROM products WHERE id=?", (ident,))
            self._event(conn, "product_deleted", ident, f"상품 삭제: {old['name']}")

    def _check_review_duplicate(self, conn, review, exclude_id=None):
        if not review["external_id"]:
            return
        for existing in self._all(conn, "reviews"):
            if existing["id"] != exclude_id and all(existing[key] == review[key] for key in ("product_id", "channel", "external_id")):
                raise ConflictError("이미 입력된 외부 리뷰 ID입니다.")

    def create_review(self, data):
        clean = _review_fields(data)
        if clean["status"] not in {"pending", "drafted"}:
            raise ValidationError("리뷰는 미처리 또는 초안 상태로 추가하세요.")
        now = _now()
        result = {**clean, "id": str(uuid4()), "created_at": now, "updated_at": now, "revision": 1}
        with self._transaction() as conn:
            self._get(conn, "products", clean["product_id"])
            self._check_review_duplicate(conn, result)
            self._save(conn, "reviews", result, True)
            self._event(conn, "review_created", result["id"], "실제 고객 리뷰 입력")
        return result

    def update_review(self, ident, data, revision):
        _mapping(data, "리뷰")
        with self._transaction() as conn:
            old = self._get(conn, "reviews", ident)
            self._match_revision(old, revision)
            if data.get("product_id", old["product_id"]) != old["product_id"]:
                raise ValidationError("리뷰의 연결 상품은 변경할 수 없습니다.")
            clean = _review_fields({**old, **data})
            changed = any(clean[key] != old[key] for key in clean)
            if not changed:
                return old
            requested = data.get("status")
            if requested in {"approved", "replied"} and requested != old["status"]:
                raise ValidationError("답변 승인·완료는 승인함에서 진행하세요.")
            # Editing a reply or its source invalidates its prior approval.
            clean["status"] = "drafted" if clean["reply_draft"] else "pending"
            result = {**clean, "id": old["id"], "created_at": old["created_at"], "updated_at": _now(), "revision": old["revision"] + 1}
            self._check_review_duplicate(conn, result, ident)
            self._save(conn, "reviews", result)
            self._event(conn, "review_updated", ident, "리뷰·답변 초안 수정")
        return result

    def request_approval(self, kind, product_id, review_id=None):
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValidationError("승인 요청 종류가 올바르지 않습니다.")
        if (kind == "review_reply") != bool(review_id):
            raise ValidationError("리뷰 답변 승인에만 리뷰 ID가 필요합니다.")
        with self._transaction() as conn:
            product = self._get(conn, "products", product_id)
            for current in self._all(conn, "approvals"):
                if current["kind"] == kind and current["product_id"] == product_id and current.get("review_id") == review_id and current["status"] in {"pending", "approved"}:
                    raise ConflictError("이미 검토 중이거나 승인된 요청입니다. 기존 요청을 완료하거나 반려하세요.")
            payload = {"product_revision": product["revision"], "product": product, "execution_mode": "manual"}
            if kind == "purchase":
                if product["status"] != "selected":
                    raise ValidationError("상품을 선정 상태로 변경한 뒤 사입 검토를 요청하세요.")
                if product["quantity"] < product["moq"]:
                    raise ValidationError("사입 수량은 최소 주문 수량 이상이어야 합니다.")
                if not product["supplier"].strip() or product["costs"]["purchase_price"] <= 0:
                    raise ValidationError("사입처와 0보다 큰 매입 단가를 입력하세요.")
                payload["document"] = build_purchase(product)
                payload["margin"] = calculate_margin(product["costs"])
            elif kind == "listing":
                if product["status"] not in {"received", "listing_ready"}:
                    raise ValidationError("입고 확인 후 상품 등록 검토를 요청하세요.")
                document = build_listing(product)
                if document.get("missing_fields"):
                    raise ValidationError("상품 등록 필수 항목을 확인하세요: " + ", ".join(document["missing_fields"]))
                payload["document"] = document
            else:
                review = self._get(conn, "reviews", review_id)
                if review["product_id"] != product_id:
                    raise ValidationError("리뷰와 상품이 일치하지 않습니다.")
                if not review["reply_draft"].strip() or review["status"] == "replied":
                    raise ValidationError("미답변 리뷰의 답변 초안을 먼저 작성하세요.")
                payload.update({"review_revision": review["revision"], "review": review, "document": {"text": review["reply_draft"]}})
            now = _now()
            result = {"id": str(uuid4()), "kind": kind, "product_id": product_id, "review_id": review_id,
                      "payload": payload, "status": "pending", "revision": 1, "created_at": now, "updated_at": now}
            self._save(conn, "approvals", result, True)
            self._event(conn, "approval_requested", result["id"], "검토 요청", {"kind": kind})
        return result

    def decide_approval(self, ident, decision, revision):
        if not isinstance(decision, str) or decision not in {"approved", "rejected", "completed"}:
            raise ValidationError("승인·반려·수동 완료 중 하나를 선택하세요.")
        with self._transaction() as conn:
            old = self._get(conn, "approvals", ident)
            self._match_revision(old, revision)
            allowed = {"pending": {"approved", "rejected"}, "approved": {"completed", "rejected"}, "rejected": set(), "completed": set()}
            if decision not in allowed[old["status"]]:
                raise ConflictError("이미 처리되었거나 올바르지 않은 승인 상태입니다.")
            result = self._bump(old)
            result["status"] = decision
            if decision != "rejected":
                product = self._get(conn, "products", old["product_id"])
                payload = dict(old["payload"])
                if product["revision"] != payload["product_revision"]:
                    raise ConflictError("검토 요청 후 상품이 변경되었습니다. 기존 요청을 반려하고 다시 요청하세요.")
                if old["kind"] == "review_reply":
                    review = self._get(conn, "reviews", old["review_id"])
                    if review["revision"] != payload["review_revision"]:
                        raise ConflictError("검토 요청 후 리뷰·답변이 변경되었습니다. 다시 요청하세요.")
                    review = self._bump(review)
                    review["status"] = "approved" if decision == "approved" else "replied"
                    self._save(conn, "reviews", review)
                    payload["review_revision"] = review["revision"]
                else:
                    product = self._bump(product)
                    product["status"] = {("purchase", "approved"): "purchase_approved", ("purchase", "completed"): "ordered", ("listing", "approved"): "listing_ready", ("listing", "completed"): "listed"}[(old["kind"], decision)]
                    self._save(conn, "products", product)
                    payload["product_revision"] = product["revision"]
                result["payload"] = payload
            if decision == "rejected" and old["status"] == "approved":
                # Undo only the status this approval owns; preserve all later field edits.
                product = self._get(conn, "products", old["product_id"])
                if old["kind"] == "review_reply":
                    review = self._get(conn, "reviews", old["review_id"])
                    if review["revision"] == old["payload"]["review_revision"] and review["status"] == "approved":
                        review = self._bump(review)
                        review["status"] = "drafted"
                        self._save(conn, "reviews", review)
                else:
                    expected = "purchase_approved" if old["kind"] == "purchase" else "listing_ready"
                    if product["status"] == expected:
                        product = self._bump(product)
                        product["status"] = "selected" if old["kind"] == "purchase" else "received"
                        self._save(conn, "products", product)
            self._save(conn, "approvals", result)
            summary = {"approved": "검토 승인 · 외부 실행 대기", "rejected": "검토 반려", "completed": "외부에서 직접 처리한 작업의 수동 완료 기록"}[decision]
            self._event(conn, "approval_" + decision, ident, summary, {"kind": old["kind"], "execution_mode": "manual"})
        return result

    def export_backup(self):
        with self._transaction() as conn:
            return {"schema_version": 1, "exported_at": _now(), **{name: self._all(conn, name) for name in ("products", "reviews", "approvals", "events")}}

    @staticmethod
    def _validate_metadata(item):
        _id(item.get("id"))
        _revision(item.get("revision"))
        _timestamp(item.get("created_at"))
        _timestamp(item.get("updated_at"))

    def import_backup(self, data):
        _mapping(data, "백업")
        if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
            raise ValidationError("지원하지 않는 백업 버전입니다.")
        _encode(data)  # reject NaN and non-JSON before touching existing records
        records = {}
        for table in ("products", "reviews", "approvals", "events"):
            items = data.get(table)
            if not isinstance(items, list) or len(items) > 100000:
                raise ValidationError("백업 데이터 목록이 올바르지 않습니다.")
            seen = set()
            records[table] = []
            for item in items:
                _mapping(item, "백업 항목")
                ident = _id(item.get("id"))
                if ident in seen:
                    raise ValidationError("백업에 중복 ID가 있습니다.")
                seen.add(ident)
                if table != "events":
                    self._validate_metadata(item)
                if table == "products":
                    clean = normalize_product(item)
                    if clean["status"] not in PRODUCT_STATUSES:
                        raise ValidationError("상품 상태가 올바르지 않습니다.")
                    item = {**clean, **{key: item[key] for key in ("id", "revision", "created_at", "updated_at")}}
                elif table == "reviews":
                    clean = _review_fields(item)
                    item = {**clean, **{key: item[key] for key in ("id", "revision", "created_at", "updated_at")}}
                elif table == "approvals":
                    if not isinstance(item.get("kind"), str) or item["kind"] not in KINDS or not isinstance(item.get("status"), str) or item["status"] not in APPROVAL_STATUSES:
                        raise ValidationError("승인 종류·상태가 올바르지 않습니다.")
                    _id(item.get("product_id"))
                    if (item["kind"] == "review_reply") != bool(item.get("review_id")):
                        raise ValidationError("승인의 리뷰 연결이 올바르지 않습니다.")
                    payload = _mapping(item.get("payload"), "승인 스냅샷")
                    _revision(payload.get("product_revision"))
                    if payload.get("execution_mode") != "manual":
                        raise ValidationError("백업의 실행 모드가 올바르지 않습니다.")
                    snap = _mapping(payload.get("product"), "상품 스냅샷")
                    self._validate_metadata(snap)
                    normalize_product(snap)
                    if snap["id"] != item["product_id"]:
                        raise ValidationError("승인 상품 스냅샷이 일치하지 않습니다.")
                    _mapping(payload.get("document"), "검토 문서")
                    if item["kind"] == "review_reply":
                        _id(item["review_id"])
                        _revision(payload.get("review_revision"))
                        review_snap = _mapping(payload.get("review"), "리뷰 스냅샷")
                        self._validate_metadata(review_snap)
                        _review_fields(review_snap)
                        if review_snap["id"] != item["review_id"] or review_snap["product_id"] != item["product_id"]:
                            raise ValidationError("리뷰 스냅샷이 일치하지 않습니다.")
                else:
                    _timestamp(item.get("created_at"))
                    _text(item.get("type"), "이벤트 종류", True, 200)
                    _text(item.get("entity_id"), "이벤트 대상", True, 200)
                    _text(item.get("summary"), "이벤트 설명", True, 20000)
                    _mapping(item.get("data", {}), "이벤트 데이터")
                records[table].append(item)
        products = {item["id"]: item for item in records["products"]}
        reviews = {item["id"]: item for item in records["reviews"]}
        external = set()
        for item in records["reviews"]:
            if item["product_id"] not in products:
                raise ValidationError("리뷰가 존재하지 않는 상품을 참조합니다.")
            if item["external_id"]:
                key = (item["product_id"], item["channel"], item["external_id"])
                if key in external:
                    raise ValidationError("중복 외부 리뷰 ID가 있습니다.")
                external.add(key)
        active = set()
        for item in records["approvals"]:
            if item["product_id"] not in products:
                raise ValidationError("승인이 존재하지 않는 상품을 참조합니다.")
            review_id = item.get("review_id")
            if review_id and (review_id not in reviews or reviews[review_id]["product_id"] != item["product_id"]):
                raise ValidationError("승인과 리뷰의 상품 연결이 일치하지 않습니다.")
            if item["status"] in {"pending", "approved"}:
                key = (item["kind"], item["product_id"], review_id)
                if key in active:
                    raise ValidationError("중복된 활성 승인 요청이 있습니다.")
                active.add(key)
        with self._transaction() as conn:
            for table in ("approvals", "reviews", "products", "events"):
                conn.execute(f"DELETE FROM {table}")
            for table in ("products", "reviews", "approvals", "events"):
                for item in records[table]:
                    self._save(conn, table, item, True)
            self._event(conn, "backup_imported", str(uuid4()), "사용자가 확인한 백업으로 데이터 교체")
        return {"imported": {name: len(items) for name, items in records.items()}}
