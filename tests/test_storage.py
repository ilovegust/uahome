import copy
from concurrent.futures import ThreadPoolExecutor
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4

from uahome.domain import ValidationError
from uahome.storage import Store, ConflictError, MissingError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data.db")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def product(self, **overrides):
        data = {"name": "테스트 수납함", "supplier": "국내 공급처", "status": "selected",
                "category": "수납", "channel": "판매처", "selling_points": "적층 가능", "specs": "30x20cm",
                "quantity": 10, "moq": 5,
                "costs": {"sale_price": 20000, "purchase_price": 7000}}
        data.update(overrides)
        return self.store.create_product(data)

    def review(self, product, **overrides):
        data = {"product_id": product["id"], "rating": 2, "content": "배송 중 손상되었어요", "reply_draft": "상태 확인 후 안내드리겠습니다.", "status": "drafted"}
        data.update(overrides)
        return self.store.create_review(data)

    def find(self, table, ident):
        return next(x for x in self.store.get_state()[table] if x["id"] == ident)

    def test_persist_and_derived_state(self):
        p = self.product()
        reopened = Store(Path(self.temp.name) / "data.db")
        self.addCleanup(reopened.close)
        state = reopened.get_state()
        self.assertEqual(state["products"][0]["id"], p["id"])
        self.assertGreater(state["products"][0]["margin"]["profit"], 0)
        self.assertIn("score", state["products"][0]["ranking"])
        self.assertTrue(all(c["status"] == "not_connected" for c in state["connectors"]))

    def test_revision_and_id_cannot_be_overwritten(self):
        p = self.product()
        changed = self.store.update_product(p["id"], {"name": "수정", "id": str(uuid4()), "revision": 900}, p["revision"])
        self.assertEqual(changed["id"], p["id"])
        self.assertEqual(changed["revision"], 2)
        with self.assertRaises(ConflictError):
            self.store.update_product(p["id"], {"notes": "오래된 변경"}, 1)
        with self.assertRaises(ValidationError):
            self.store.delete_product(p["id"], True)

    def test_noop_does_not_create_event(self):
        p = self.product()
        before = len(self.store.get_state()["events"])
        same = self.store.update_product(p["id"], {"name": p["name"]}, 1)
        self.assertEqual(same["revision"], 1)
        self.assertEqual(len(self.store.get_state()["events"]), before)

    def test_concurrent_update_only_one_wins(self):
        p = self.product()
        def change(name):
            try:
                self.store.update_product(p["id"], {"name": name}, 1)
                return "ok"
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ["수정A", "수정B"]))
        self.assertCountEqual(results, ["ok", "conflict"])

    def test_purchase_approval_then_manual_completion(self):
        p = self.product()
        approval = self.store.request_approval("purchase", p["id"])
        with self.assertRaises(ConflictError):
            self.store.request_approval("purchase", p["id"])
        with self.assertRaises(ConflictError):
            self.store.decide_approval(approval["id"], "completed", 1)
        approved = self.store.decide_approval(approval["id"], "approved", 1)
        self.assertEqual(self.find("products", p["id"])["status"], "purchase_approved")
        completed = self.store.decide_approval(approval["id"], "completed", approved["revision"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.find("products", p["id"])["status"], "ordered")
        self.assertEqual(completed["payload"]["execution_mode"], "manual")
        with self.assertRaises(ConflictError):
            self.store.decide_approval(approval["id"], "completed", completed["revision"])
        ordered = self.find("products", p["id"])
        received = self.store.update_product(p["id"], {"status": "received"}, ordered["revision"])
        self.assertEqual(received["status"], "received")

    def test_stale_approval_is_blocked_and_rejection_allows_new_request(self):
        p = self.product()
        a = self.store.request_approval("purchase", p["id"])
        self.store.update_product(p["id"], {"costs": {"purchase_price": 8000}}, 1)
        with self.assertRaises(ConflictError):
            self.store.decide_approval(a["id"], "approved", 1)
        self.store.decide_approval(a["id"], "rejected", 1)
        new = self.store.request_approval("purchase", p["id"])
        approved = self.store.decide_approval(new["id"], "approved", 1)
        current = self.find("products", p["id"])
        self.store.update_product(p["id"], {"notes": "발주 수량 다시 확인"}, current["revision"])
        with self.assertRaises(ConflictError):
            self.store.decide_approval(new["id"], "completed", approved["revision"])
        self.store.decide_approval(new["id"], "rejected", approved["revision"])
        current = self.find("products", p["id"])
        self.assertEqual(current["status"], "selected")
        self.assertEqual(current["notes"], "발주 수량 다시 확인")
        self.store.request_approval("purchase", p["id"])

    def test_cancel_approved_purchase_returns_to_selected(self):
        p = self.product()
        a = self.store.request_approval("purchase", p["id"])
        a = self.store.decide_approval(a["id"], "approved", 1)
        self.store.decide_approval(a["id"], "rejected", a["revision"])
        self.assertEqual(self.find("products", p["id"])["status"], "selected")
        self.store.request_approval("purchase", p["id"])

    def test_status_and_purchase_validation(self):
        p = self.product()
        with self.assertRaises(ValidationError):
            self.store.update_product(p["id"], {"status": "listed"}, 1)
        with self.assertRaises(ValidationError):
            self.store.create_product({"name": "무단 등록", "status": "ordered"})
        small = self.product(quantity=1)
        with self.assertRaises(ValidationError):
            self.store.request_approval("purchase", small["id"])
        with self.assertRaises(ValidationError):
            self.store.request_approval([], p["id"])

    def test_listing_after_receiving(self):
        p = self.product()
        a = self.store.request_approval("purchase", p["id"])
        a = self.store.decide_approval(a["id"], "approved", 1)
        self.store.decide_approval(a["id"], "completed", a["revision"])
        current = self.find("products", p["id"])
        self.store.update_product(p["id"], {"status": "received"}, current["revision"])
        listing = self.store.request_approval("listing", p["id"])
        listing = self.store.decide_approval(listing["id"], "approved", 1)
        self.store.decide_approval(listing["id"], "completed", listing["revision"])
        self.assertEqual(self.find("products", p["id"])["status"], "listed")

    def test_reviews_must_match_product_and_reply_must_be_reviewed(self):
        p = self.product()
        r = self.review(p)
        other = self.product(name="다른 상품")
        with self.assertRaises(ValidationError):
            self.store.request_approval("review_reply", other["id"], r["id"])
        a = self.store.request_approval("review_reply", p["id"], r["id"])
        a = self.store.decide_approval(a["id"], "approved", 1)
        self.store.decide_approval(a["id"], "completed", a["revision"])
        self.assertEqual(self.find("reviews", r["id"])["status"], "replied")
        with self.assertRaises(ValidationError):
            self.store.create_review({"product_id": p["id"], "rating": 3, "content": "실제", "status": []})

    def test_review_edit_invalidates_prior_approval(self):
        p = self.product()
        r = self.review(p)
        a = self.store.request_approval("review_reply", p["id"], r["id"])
        a = self.store.decide_approval(a["id"], "approved", 1)
        r = self.find("reviews", r["id"])
        updated = self.store.update_review(r["id"], {"reply_draft": "다시 검토할 새 답변"}, r["revision"])
        self.assertEqual(updated["status"], "drafted")
        with self.assertRaises(ConflictError):
            self.store.decide_approval(a["id"], "completed", a["revision"])

    def test_duplicate_review_id_and_delete_protection(self):
        p = self.product()
        self.review(p, external_id="review-123", channel="판매처")
        with self.assertRaises(ConflictError):
            self.review(p, external_id="review-123", channel="판매처")
        with self.assertRaises(ConflictError):
            self.store.delete_product(p["id"], 1)
        empty = self.product()
        self.store.delete_product(empty["id"], 1)
        with self.assertRaises(MissingError):
            self.store.delete_product(empty["id"], 1)

    def test_backup_roundtrip_preserves_approvals_and_manual_mode(self):
        p = self.product()
        self.review(p)
        a = self.store.request_approval("purchase", p["id"])
        self.store.decide_approval(a["id"], "approved", 1)
        data = self.store.export_backup()
        destination = Store(":memory:")
        self.addCleanup(destination.close)
        imported = destination.import_backup(data)
        self.assertEqual(imported["imported"]["products"], 1)
        self.assertEqual(destination.get_state()["products"][0]["name"], p["name"])
        a = destination.get_state()["approvals"][0]
        destination.decide_approval(a["id"], "completed", a["revision"])

    def test_listing_to_fulfillment_inbound_manual_flow_and_backup(self):
        p = self.product()
        purchase = self.store.request_approval("purchase", p["id"])
        purchase = self.store.decide_approval(purchase["id"], "approved", purchase["revision"])
        self.store.decide_approval(purchase["id"], "completed", purchase["revision"])
        current = self.find("products", p["id"])
        self.store.update_product(p["id"], {"status": "received"}, current["revision"])
        listing = self.store.request_approval("listing", p["id"])
        listing = self.store.decide_approval(listing["id"], "approved", listing["revision"])
        self.store.decide_approval(listing["id"], "completed", listing["revision"])
        current = self.find("products", p["id"])
        self.assertEqual(current["status"], "listed")
        identifiers = {"seller_sku": "UA-001", "coupang_product_id": "1234567890",
                       "fulfillment_center": "예시 물류센터", "inbound_reference": "INBOUND-001",
                       "tracking_number": "TRACK-001"}
        current = self.store.update_product(p["id"], {**identifiers, "status": "inbound_planned"}, current["revision"])
        # An inbound plan can be cancelled before dispatch, preserving identifiers.
        current = self.store.update_product(p["id"], {"status": "listed"}, current["revision"])
        current = self.store.update_product(p["id"], {"status": "inbound_planned"}, current["revision"])
        with self.assertRaises(ValidationError):
            self.store.update_product(p["id"], {"status": "stocked"}, current["revision"])
        current = self.store.update_product(p["id"], {"status": "in_transit"}, current["revision"])
        current = self.store.update_product(p["id"], {"status": "stocked"}, current["revision"])
        self.assertEqual({key: current[key] for key in identifiers}, identifiers)
        state = self.store.get_state()
        center_events = [event for event in state["events"] if event["summary"].startswith("센터 입고 단계 수동 기록")]
        self.assertEqual(len(center_events), 4)
        self.assertTrue(all(event["data"]["execution_mode"] == "manual" for event in center_events))
        self.assertEqual(center_events[0]["data"]["to_status"], "stocked")
        backup = self.store.export_backup()
        destination = Store(":memory:")
        self.addCleanup(destination.close)
        destination.import_backup(backup)
        restored = destination.get_state()["products"][0]
        self.assertEqual(restored["status"], "stocked")
        self.assertEqual({key: restored[key] for key in identifiers}, identifiers)
        paused = destination.update_product(restored["id"], {"status": "paused"}, restored["revision"])
        self.assertEqual(paused["status"], "paused")

    def test_backup_from_before_fulfillment_fields_remains_compatible(self):
        p = self.product()
        backup = self.store.export_backup()
        fields = ("seller_sku", "coupang_product_id", "fulfillment_center", "inbound_reference", "tracking_number")
        for item in backup["products"]:
            for key in fields:
                item.pop(key, None)
        self.store.import_backup(backup)
        restored = self.find("products", p["id"])
        self.assertEqual(restored["status"], "selected")
        self.assertTrue(all(restored[key] == "" for key in fields))

    def test_invalid_backup_leaves_original_data_unchanged(self):
        p = self.product()
        self.review(p)
        original = self.store.export_backup()
        for corrupt in ("orphan", "duplicate", "nan", "bad_state", "missing"):
            with self.subTest(corrupt=corrupt):
                backup = copy.deepcopy(original)
                if corrupt == "orphan":
                    backup["reviews"][0]["product_id"] = str(uuid4())
                elif corrupt == "duplicate":
                    backup["products"].append(backup["products"][0])
                elif corrupt == "nan":
                    backup["products"][0]["costs"]["purchase_price"] = float("nan")
                elif corrupt == "bad_state":
                    backup["reviews"][0]["status"] = "invented"
                else:
                    del backup["events"]
                with self.assertRaises(ValidationError):
                    self.store.import_backup(backup)
                state = self.store.export_backup()
                self.assertEqual(state["products"], original["products"])
                self.assertEqual(state["reviews"], original["reviews"])
                self.assertEqual(state["events"], original["events"])


if __name__ == "__main__":
    unittest.main()
