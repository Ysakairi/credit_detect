"""LLM 出力パースの回帰。構造化出力 API に依存せずグラフを進めるため。

【Agent Engine 上の位置づけ】
Flash 系はフェンスや前置き文を付けがち。パース失敗を例外にすると query() が落ちる。
Reflection は JSON 化失敗を成功扱いにしない契約をここで固定する。

【主な構成】
- extract_sql: フェンス優先
- numbered_plan: 番号剥離
- parse_reflection: 不足 JSON を三値へ
- extract_json: フェンス内オブジェクト
"""

import unittest

from agent.parsing import extract_json, extract_sql, numbered_plan, parse_reflection


class ParsingTest(unittest.TestCase):
    def test_extract_sql_from_fence(self):
        """説明文に埋まった SQL だけを実行・是正タブへ渡せることを確認する。"""
        text = "here\n```sql\nSELECT 1\n```\n"
        self.assertEqual(extract_sql(text), "SELECT 1")

    def test_numbered_plan(self):
        """Planner の番号を剥がし、SQL Gen が手順を条件に翻訳しやすくする。"""
        text = "1. 抽出する\n2. 規程を見る\n"
        self.assertEqual(numbered_plan(text), ["抽出する", "規程を見る"])

    def test_parse_reflection_json(self):
        """不足判定と critique を条件辺が読める形に正規化すること。黙って十分にしない。"""
        ok, score, critique = parse_reflection(
            '{"is_sufficient": false, "score": 40, "critique": "件数が足りない"}'
        )
        self.assertFalse(ok)
        self.assertEqual(score, 40)
        self.assertIn("件数", critique)

    def test_extract_json_fenced(self):
        """前置き付きフェンスから dict を拾い、Reflection の JSON のみ要求を緩和する。"""
        parsed = extract_json("```json\n{\"a\": 1}\n```")
        self.assertEqual(parsed["a"], 1)


if __name__ == "__main__":
    unittest.main()
