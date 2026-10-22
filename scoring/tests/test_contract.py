import json
from pathlib import Path
import unittest


class ContractTests(unittest.TestCase):
    def test_schema_is_valid_json_with_expected_fields(self):
        schema = json.loads(Path("contracts/scoring.schema.json").read_text())
        self.assertIn("application_id", schema["required"])
        self.assertIn("monthly_revenue", schema["required"])


if __name__ == "__main__":
    unittest.main()
