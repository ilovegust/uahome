"""Exercise the running local HTTP app, including workflow and trust boundaries."""
import csv
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from uahome.server import AppServer
from uahome.storage import Store


class HttpAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "operations.sqlite3")
        self.server = AppServer(("127.0.0.1", 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.directory.cleanup()

    def request(self, method, path, data=None, raw=None, headers=None):
        request_headers = dict(headers or {})
        body = raw
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            response_headers = dict(response.getheaders())
            if response_headers.get("Content-Type", "").startswith("application/json"):
                content = json.loads(content)
            return response.status, response_headers, content
        finally:
            connection.close()

    def ok(self, method, path, data=None):
        status, _, result = self.request(method, path, data)
        self.assertEqual(status, 200, result)
        return result["data"]

    def product(self, **overrides):
        fields = {
            "name": "수납 바스켓", "category": "수납", "supplier": "국내 도매",
            "source_url": "https://example.com/basket", "channel": "스마트스토어",
            "specs": "30cm / 면 / 베이지", "selling_points": "접어서 보관",
            "keywords": "정리,수납", "quantity": 10, "moq": 5,
            "costs": {"sale_price": 10000, "purchase_price": 4000,
                      "outbound_shipping": 1000, "platform_fee_rate": 10},
        }
        fields.update(overrides)
        return self.ok("POST", "/api/products", fields)

    def test_initial_state_is_empty_and_connections_are_honest(self):
        state = self.ok("GET", "/api/state")
        for key in ("products", "reviews", "approvals", "events"):
            self.assertEqual(state[key], [])
        self.assertTrue(state["connectors"])
        self.assertTrue(all(c["status"] == "not_connected" for c in state["connectors"]))

    def test_explicit_demo_preserves_existing_data_and_is_idempotent(self):
        own = self.product()
        self.assertTrue(self.ok("POST", "/api/demo", {})["created"])
        self.assertFalse(self.ok("POST", "/api/demo", {})["created"])
        state = self.ok("GET", "/api/state")
        self.assertEqual(len(state["products"]), 2)
        self.assertEqual(len(state["reviews"]), 1)
        self.assertIn(own["id"], {p["id"] for p in state["products"]})
        self.assertIn("예시", state["reviews"][0]["content"])

    def test_product_crud_and_revision_conflict(self):
        product = self.product()
        path = "/api/products/" + product["id"]
        changed = self.ok("PATCH", path, {"name": "새 수납함", "revision": product["revision"]})
        self.assertEqual(changed["name"], "새 수납함")
        status, _, error = self.request("PATCH", path, {"name": "오래된 변경", "revision": product["revision"]})
        self.assertEqual(status, 409, error)
        self.assertEqual(self.ok("GET", "/api/state")["products"][0]["name"], "새 수납함")
        self.assertTrue(self.ok("DELETE", path, {"revision": changed["revision"]})["deleted"])
        self.assertEqual(self.ok("GET", "/api/state")["products"], [])

    def test_margin_uses_all_revenue_and_rejects_bad_numbers(self):
        margin = self.ok("POST", "/api/calculate", {"costs": {
            "sale_price": 10000, "purchase_price": 4000,
            "outbound_shipping": 1000, "platform_fee_rate": 10,
        }})
        self.assertEqual(margin["revenue"], 10000)
        self.assertEqual(margin["profit"], 4000)
        self.assertEqual(margin["margin_rate"], 40)
        for bad in (-1, "NaN", "Infinity", True):
            with self.subTest(value=bad):
                status, _, body = self.request("POST", "/api/calculate", {"costs": {"purchase_price": bad}})
                self.assertEqual(status, 400, body)
        self.assertEqual(self.ok("GET", "/api/state")["products"], [])

    def test_assist_prepares_reviews_and_purchase_without_approving(self):
        self.ok("POST", "/api/demo", {})
        state = self.ok("GET", "/api/state")
        product = state["products"][0]
        self.ok("PATCH", "/api/products/" + product["id"], {
            "status": "selected", "revision": product["revision"],
        })
        prepared = self.ok("POST", "/api/assist", {})
        self.assertEqual({item["kind"] for item in prepared["prepared"]}, {"review_draft", "purchase"})
        state = self.ok("GET", "/api/state")
        self.assertTrue(state["reviews"][0]["reply_draft"])
        self.assertEqual(state["reviews"][0]["status"], "drafted")
        self.assertEqual(state["products"][0]["status"], "selected")
        self.assertEqual(state["approvals"][0]["status"], "pending")
        repeat = self.ok("POST", "/api/assist", {})
        self.assertEqual(repeat["prepared"], [])
        self.assertEqual(len(self.ok("GET", "/api/state")["approvals"]), 1)

    def test_manual_approval_workflow_and_listing_draft(self):
        product = self.product(status="selected")
        purchase = self.ok("POST", "/api/products/" + product["id"] + "/purchase", {})
        self.assertEqual(purchase["quantity"], 10)
        self.assertIn("미전송", purchase["text"])
        approval = self.ok("POST", "/api/approvals", {"kind": "purchase", "product_id": product["id"]})
        path = "/api/approvals/" + approval["id"] + "/decision"
        approved = self.ok("POST", path, {"decision": "approved", "revision": approval["revision"]})
        self.assertEqual(approved["status"], "approved")
        completed = self.ok("POST", path, {"decision": "completed", "revision": approved["revision"]})
        self.assertEqual(completed["status"], "completed")
        state = self.ok("GET", "/api/state")
        ordered = state["products"][0]
        self.assertEqual(ordered["status"], "ordered")
        received = self.ok("PATCH", "/api/products/" + product["id"], {
            "status": "received", "revision": ordered["revision"],
        })
        self.assertEqual(received["status"], "received")
        draft = self.ok("POST", "/api/products/" + product["id"] + "/listing", {})
        self.assertEqual(draft["mode"], "draft")
        self.assertIn("접어서 보관", draft["description"])
        listing = self.ok("POST", "/api/approvals", {"kind": "listing", "product_id": product["id"]})
        self.assertEqual(listing["status"], "pending")

    def test_real_review_reply_needs_explicit_save_and_approval(self):
        product = self.product()
        review = self.ok("POST", "/api/reviews", {
            "product_id": product["id"], "rating": 1, "content": "제품이 파손되어 왔어요.",
        })
        draft = self.ok("POST", "/api/reviews/" + review["id"] + "/draft", {})
        self.assertTrue(draft["requires_attention"])
        self.assertEqual(self.ok("GET", "/api/state")["reviews"][0]["reply_draft"], "")
        saved = self.ok("PATCH", "/api/reviews/" + review["id"], {
            "reply_draft": draft["text"], "revision": review["revision"],
        })
        self.assertEqual(saved["status"], "drafted")
        approval = self.ok("POST", "/api/approvals", {
            "kind": "review_reply", "product_id": product["id"], "review_id": review["id"],
        })
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(self.ok("GET", "/api/state")["reviews"][0]["status"], "drafted")

    def test_csv_escapes_spreadsheet_formulas_and_contains_real_margins(self):
        self.product(name="=HYPERLINK(\"https://example.com\")", supplier="@malicious")
        status, headers, content = self.request("GET", "/api/export/products.csv")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        self.assertTrue(rows[1][1].startswith("'="))
        self.assertTrue(rows[1][2].startswith("'@"))
        self.assertEqual(float(rows[1][6]), 4000)
        status, _, content = self.request("GET", "/api/export/listings.csv")
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        self.assertTrue(rows[1][2].startswith("'="))

    def test_backup_roundtrip_and_invalid_import_does_not_destroy_data(self):
        product = self.product()
        backup = self.ok("GET", "/api/backup")
        self.product(name="복원 시 없어질 상품")
        status, _, body = self.request("POST", "/api/import", {"backup": {"schema_version": 1, "products": "bad"}})
        self.assertEqual(status, 400, body)
        self.assertEqual(len(self.ok("GET", "/api/state")["products"]), 2)
        self.ok("POST", "/api/import", {"backup": backup})
        restored = self.ok("GET", "/api/state")["products"]
        self.assertEqual([p["id"] for p in restored], [product["id"]])

    def test_cross_origin_and_untrusted_hosts_cannot_read_or_write(self):
        status, _, body = self.request("POST", "/api/demo", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403, body)
        status, _, body = self.request("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 400, body)
        status, _, body = self.request("POST", "/api/demo", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403, body)
        self.assertEqual(self.ok("GET", "/api/state")["products"], [])

    def test_downloaded_backup_file_can_be_restored_without_manual_unwrapping(self):
        product = self.product()
        status, headers, downloaded = self.request("GET", "/api/backup")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.product(name="다운로드 후 추가한 상품")
        self.ok("POST", "/api/import", {"backup": downloaded})
        self.assertEqual([p["id"] for p in self.ok("GET", "/api/state")["products"]], [product["id"]])

    def test_import_quote_api_and_rocket_growth_costs_persist(self):
        quote = self.ok("POST", "/api/import/calculate", {
            "quantity": 100, "cny_unit_price": 10, "exchange_rate": 200,
            "international_shipping_krw": 30000, "customs_duty_krw": 10000,
            "import_vat_krw": 24000, "vat_recoverable": True})
        self.assertEqual(quote["cash_outlay_total"], 264000)
        self.assertEqual(quote["operating_cost_total"], 240000)
        product = self.product(supplier="1688", channel="쿠팡 로켓그로스", quantity=100,
                               costs={**quote["costs_patch"], "sale_price": 10000,
                                      "fulfillment_fee": 2000, "storage_fee": 100})
        stored = self.ok("GET", "/api/state")["products"][0]
        self.assertEqual(stored["id"], product["id"])
        self.assertEqual(stored["margin"]["landed_cost"], 2400)
        self.assertEqual(stored["margin"]["profit"], 5500)

    def test_malformed_json_and_non_json_are_rejected(self):
        for raw in (b"{", b"[]", b"null", b'{"sale_price": NaN}'):
            with self.subTest(raw=raw):
                status, _, body = self.request("POST", "/api/calculate", raw=raw,
                                               headers={"Content-Type": "application/json"})
                self.assertEqual(status, 400, body)
        status, _, body = self.request("POST", "/api/demo", raw=b"{}",
                                       headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400, body)

    def test_static_allowlist_mime_and_security_headers(self):
        for path, mime in (("/", "text/html"), ("/styles.css", "text/css"), ("/app.js", "text/javascript")):
            with self.subTest(path=path):
                status, headers, content = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(mime))
                self.assertTrue(content)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
        for path in ("/../AGENTS.md", "/%2e%2e/AGENTS.md", "/data/uahome.sqlite3", "/.env"):
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 404, body)


if __name__ == "__main__":
    unittest.main()
