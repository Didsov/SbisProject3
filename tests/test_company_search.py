from __future__ import annotations

import unittest

from src.sbis.company_search import _extract_company_uuid_by_inn


class CompanySearchTests(unittest.TestCase):
    def test_exact_inn_match_is_required_and_normalized(self) -> None:
        result = {
            "s": [{"n": "ИНН"}, {"n": "SppUuid"}],
            "d": [
                ["7725806890", "2f0bc96d-7808-40ae-aaea-1952613bd8f2"],
                ["77 258 068 98", "4E450D9B-81B7-4BC4-B71A-A5DFB83116A8"],
            ],
        }

        self.assertEqual(
            _extract_company_uuid_by_inn(result, "7725806898"),
            "4e450d9b-81b7-4bc4-b71a-a5dfb83116a8",
        )

    def test_similar_inn_is_not_accepted(self) -> None:
        result = {
            "s": [{"n": "ИНН"}, {"n": "SppUuid"}],
            "d": [["7725806890", "2f0bc96d-7808-40ae-aaea-1952613bd8f2"]],
        }

        self.assertIsNone(_extract_company_uuid_by_inn(result, "7725806898"))


if __name__ == "__main__":
    unittest.main()
