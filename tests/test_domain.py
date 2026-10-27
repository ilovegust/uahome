import unittest

from uahome.domain import (
    ValidationError, build_listing, build_purchase, calculate_margin,
    draft_review_reply, normalize_product, rank_product,
)


class MarginTests(unittest.TestCase):
    def test_rocket_growth_fulfillment_and_storage_fees_reduce_profit_per_unit(self):
        costs = {"sale_price": 20000, "purchase_price": 5000,
                 "target_margin_rate": 20}
        base = calculate_margin(costs)
        charged = calculate_margin({**costs, "fulfillment_fee": 2500, "storage_fee": 300})
        self.assertEqual(charged["total_cost"] - base["total_cost"], 2800)
        self.assertEqual(base["profit"] - charged["profit"], 2800)
        self.assertEqual(charged["margin_rate"], 61)
        self.assertEqual(charged["break_even_price"], 7800)
        self.assertEqual(charged["recommended_price"], 9750)
        self.assertTrue(any("중복" in assumption for assumption in charged["assumptions"]))
        for field in ("fulfillment_fee", "storage_fee"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                calculate_margin({field: -1})

    def test_shipping_and_every_variable_fee_use_total_revenue(self):
        result = calculate_margin({
            "sale_price": 20000, "customer_shipping": 3000,
            "purchase_price": 40, "exchange_rate": 100,
            "inbound_shipping": 500, "outbound_shipping": 3000,
            "packaging": 500, "fixed_fee": 100, "other_cost": 200,
            "return_rate": 10, "return_cost": 5000,
            "platform_fee_rate": 10, "payment_fee_rate": 2,
            "ad_rate": 5, "tax_reserve_rate": 3, "target_margin_rate": 30,
        })
        self.assertEqual(result["revenue"], 23000)
        self.assertEqual(result["landed_cost"], 4500)
        self.assertEqual(result["return_reserve"], 500)
        self.assertEqual(result["variable_cost"], 4600)
        self.assertEqual(result["total_cost"], 13400)
        self.assertEqual(result["profit"], 9600)
        self.assertEqual(result["margin_rate"], 41.74)
        self.assertEqual(result["break_even_price"], 8000)
        self.assertEqual(result["recommended_price"], 14600)

    def test_decimal_rounding_and_recommended_price_reaches_target(self):
        costs = {"purchase_price": 0.1, "inbound_shipping": 0.2,
                 "platform_fee_rate": 12.3, "target_margin_rate": 33.3}
        result = calculate_margin(costs)
        self.assertEqual(result["landed_cost"], 0.3)
        actual = calculate_margin({**costs, "sale_price": result["recommended_price"]})
        self.assertGreaterEqual(actual["margin_rate"], 33.3)

    def test_unattainable_price_is_null_not_infinity(self):
        result = calculate_margin({"sale_price": 10000, "purchase_price": 5000,
                                   "platform_fee_rate": 80, "ad_rate": 20,
                                   "target_margin_rate": 20})
        self.assertIsNone(result["break_even_price"])
        self.assertIsNone(result["recommended_price"])
        self.assertEqual(result["profit"], -5000)
        self.assertGreater(len(result["warnings"]), 1)

    def test_zero_revenue_does_not_divide_by_zero(self):
        result = calculate_margin({"purchase_price": 1000})
        self.assertEqual(result["margin_rate"], 0)
        self.assertEqual(result["profit"], -1000)
        self.assertTrue(result["warnings"])

    def test_invalid_nonfinite_negative_and_out_of_range_inputs(self):
        for bad in (float("nan"), float("inf"), "NaN", "Infinity", -1, True, None, "text"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                calculate_margin({"sale_price": bad})
        for costs in ({"platform_fee_rate": 101}, {"target_margin_rate": 100}, {"exchange_rate": 0}):
            with self.subTest(costs=costs), self.assertRaises(ValidationError):
                calculate_margin(costs)

    def test_excess_precision_returns_validation_error_not_decimal_failure(self):
        for costs in ({"platform_fee_rate": "99.99999999999999"},
                      {"purchase_price": "0.0000001"},
                      {"exchange_rate": "1e-999999"}):
            with self.subTest(costs=costs), self.assertRaises(ValidationError):
                calculate_margin(costs)

    def test_smallest_fee_denominator_and_max_purchase_do_not_overflow(self):
        result = calculate_margin({"purchase_price": "1000000000000",
                                   "exchange_rate": "1000000000000",
                                   "platform_fee_rate": "99.999999"})
        self.assertEqual(result["break_even_price"], 1e32)
        self.assertEqual(result["recommended_price"], 1e32)
        purchase = build_purchase({"name": "경계값 상품", "quantity": 1000000,
                                   "costs": {"purchase_price": "1000000000000",
                                             "exchange_rate": "1000000000000"}})
        self.assertEqual(purchase["estimated_total"], 1e30)


class ProductTests(unittest.TestCase):
    def test_rocket_growth_identifiers_and_inbound_statuses_are_preserved(self):
        fields = {"seller_sku": " UAH-001 ", "coupang_product_id": "123456",
                  "fulfillment_center": "확인한 입고센터", "inbound_reference": "IN-001",
                  "tracking_number": "000123456"}
        for status in ("inbound_planned", "in_transit", "stocked", "listed"):
            with self.subTest(status=status):
                product = normalize_product({"name": "수납함", "status": status, **fields})
                self.assertEqual(product["status"], status)
                self.assertEqual(product["seller_sku"], "UAH-001")
                self.assertEqual(product["tracking_number"], "000123456")
                self.assertEqual(product["fulfillment_center"], fields["fulfillment_center"])
                self.assertEqual(build_listing(product)["mode"], "draft")
                self.assertEqual(build_purchase(product)["quantity"], 1)
        default = normalize_product({"name": "기본 상품"})
        for field in fields:
            self.assertEqual(default[field], "")

    def test_normalize_whitelists_defaults_and_validates_integers(self):
        product = normalize_product({"name": " 수납함 ", "id": "attacker", "revision": 99})
        self.assertEqual(product["name"], "수납함")
        self.assertEqual(product["quantity"], 1)
        self.assertEqual(product["costs"]["exchange_rate"], 1)
        self.assertEqual(product["costs"]["fulfillment_fee"], 0)
        self.assertEqual(product["costs"]["storage_fee"], 0)
        self.assertNotIn("id", product)
        for data in ({"quantity": 0}, {"moq": 1.5}, {"lead_days": -1},
                     {"demand_score": 6}, {"status": "bogus"}, {"status": []},
                     {"source_url": "javascript:alert(1)"},
                     {"source_url": "https://user:secret@example.com"}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                normalize_product({"name": "제품", **data})

    def test_competition_score_is_inverted_and_rank_is_not_market_prediction(self):
        p = {"name": "제품", "demand_score": 5, "supplier_score": 5,
             "costs": {"sale_price": 20000, "purchase_price": 5000, "target_margin_rate": 30}}
        low = rank_product({**p, "competition_score": 0})
        high = rank_product({**p, "competition_score": 5})
        self.assertEqual(low["score"] - high["score"], 20)
        self.assertEqual(low["score"], 100)
        self.assertIn("수동", low["reasons"][0])
        self.assertTrue(any("사용자 평가" in reason for reason in low["reasons"]))
        self.assertEqual(rank_product({"name": "제품"})["recommendation"], "비용 입력 필요")

    def test_listing_uses_only_product_facts_and_reports_missing_fields(self):
        result = build_listing({"name": "수납함", "keywords": "정리, 정리,수납",
                                "selling_points": "뚜껑 포함"})
        self.assertEqual(result["mode"], "draft")
        self.assertEqual(result["keywords"], ["정리", "수납"])
        self.assertIn("specs", result["missing_fields"])
        self.assertIn("뚜껑 포함", result["description"])
        self.assertNotIn("인증 완료", result["description"])

    def test_purchase_contains_moq_warning_and_no_sales_fees(self):
        result = build_purchase({"name": "수납함", "quantity": 3, "moq": 10,
                                 "costs": {"purchase_price": 10, "exchange_rate": 200,
                                           "inbound_shipping": 100, "outbound_shipping": 5000}})
        self.assertEqual(result["estimated_total"], 6300)
        self.assertTrue(any("MOQ" in warning for warning in result["warnings"]))
        self.assertIn("미전송", result["text"])

    def test_review_drafts_require_actual_content_and_route_complaints(self):
        result = draft_review_reply({"rating": 5, "content": "상품이 파손됐어요"}, {"name": "수납함"})
        self.assertTrue(result["requires_attention"])
        self.assertNotIn("환불해", result["text"])
        self.assertEqual(result["mode"], "draft")
        with self.assertRaises(ValidationError):
            draft_review_reply({"rating": 5, "content": ""}, {"name": "제품"})
        with self.assertRaises(ValidationError):
            draft_review_reply({"rating": 6, "content": "만족"}, {"name": "제품"})


if __name__ == "__main__":
    unittest.main()
