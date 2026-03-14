import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from database.json_utils import make_json_serializable


class JsonSerializationTests(unittest.TestCase):
    def test_converts_nested_datetime_date_and_decimal(self):
        payload = {
            "start_date": datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
            "end_date": date(2024, 1, 31),
            "fees": Decimal("0.0003"),
            "nested": [
                {"at": datetime(2024, 1, 2, 0, 0), "price": Decimal("123.45")},
            ],
        }

        result = make_json_serializable(payload)

        self.assertEqual(result["start_date"], "2024-01-01T09:30:00+00:00")
        self.assertEqual(result["end_date"], "2024-01-31")
        self.assertAlmostEqual(result["fees"], 0.0003)
        self.assertEqual(result["nested"][0]["at"], "2024-01-02T00:00:00")
        self.assertEqual(result["nested"][0]["price"], 123.45)


if __name__ == "__main__":
    unittest.main()
