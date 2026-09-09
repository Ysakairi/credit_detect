"""Parser tests."""

import unittest

from agent.parsing import extract_json, extract_sql, numbered_plan, parse_reflection


class ParsingTest(unittest.TestCase):
    def test_extract_sql_from_fence(self):
        text = "here\n```sql\nSELECT 1\n```\n"
        self.assertEqual(extract_sql(text), "SELECT 1")

    def test_numbered_plan(self):
        text = "1. 抽出する\n2. 規程を見る\n"
        self.assertEqual(numbered_plan(text), ["抽出する", "規程を見る"])

    def test_parse_reflection_json(self):
        ok, score, critique = parse_reflection(
            '{"is_sufficient": false, "score": 40, "critique": "件数が足りない"}'
        )
        self.assertFalse(ok)
        self.assertEqual(score, 40)
        self.assertIn("件数", critique)

    def test_extract_json_fenced(self):
        parsed = extract_json("```json\n{\"a\": 1}\n```")
        self.assertEqual(parsed["a"], 1)


if __name__ == "__main__":
    unittest.main()
