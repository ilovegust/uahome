import unittest

from uahome.domain import ValidationError, calculate_margin
from uahome.sourcing import calculate_import_batch


class ImportBatchTests(unittest.TestCase):
    def sample(self, **changes):
        return {"quantity": 100, "cny_unit_price": 10, "exchange_rate": 200,
                "china_shipping_cny": 50, "international_shipping_krw": 30000,
                "customs_duty_krw": 20000, "import_vat_krw": 26000,
                "brokerage_krw": 10000, "inspection_krw": 5000,
                "labeling_krw": 3000, "domestic_inbound_krw": 6000, **changes}

    def test_total_shipping_is_allocated_once_and_vat_cash_is_separate(self):
        result = calculate_import_batch(self.sample(vat_recoverable=True))
        self.assertEqual(result["purchase_total_krw"], 200000)
        self.assertEqual(result["logistics_total_krw"], 110000)
        self.assertEqual(result["cash_outlay_total"], 310000)
        self.assertEqual(result["operating_cost_total"], 284000)
        self.assertEqual(result["per_unit_landed_cost"], 2840)
        self.assertEqual(result["costs_patch"]["inbound_shipping"], 840)

    def test_nonrecoverable_vat_stays_in_operating_cost(self):
        result = calculate_import_batch(self.sample())
        self.assertEqual(result["operating_cost_total"], 310000)
        self.assertEqual(result["per_unit_inbound_cost"], 1100)

    def test_patch_applies_to_rocket_growth_margin_without_double_buying_cost(self):
        batch = calculate_import_batch(self.sample(vat_recoverable=True))
        margin = calculate_margin({**batch["costs_patch"], "sale_price": 10000,
                                   "fulfillment_fee": 2000, "storage_fee": 100})
        self.assertEqual(margin["landed_cost"], 2840)
        self.assertEqual(margin["profit"], 5060)

    def test_invalid_values_are_rejected(self):
        for changes in ({"quantity": 0}, {"quantity": 1.5}, {"exchange_rate": 0},
                        {"cny_unit_price": float("nan")}, {"china_shipping_cny": -1},
                        {"vat_recoverable": "true"}, {"cny_unit_price": "0.0000001"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                calculate_import_batch(self.sample(**changes))


if __name__ == "__main__":
    unittest.main()
