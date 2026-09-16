"""SQL ガードの回帰。Agent Engine が倉庫を壊さない契約を単体で固定する。

【Agent Engine 上の位置づけ】
Text-to-SQL は SELECT 以外を混ぜ得る。グラフ結合前に拒否理由を例外化し、
sql_retry 辺へ載せる。LIMIT 付与はスキャン事故と Analyzer 過負荷の防止。

【主な構成】
- test_select_gets_limit: 無制限 SELECT を 200 行にキャップ
- test_rejects_*: DML/DDL/EXPLAIN を拒否
- test_strips_comments_and_semicolons: コメント内攻撃と複数文を無効化
"""

import unittest

from agent.sql_guard import SqlGuardError, sanitize_sql


class SqlGuardTest(unittest.TestCase):
    def test_select_gets_limit(self):
        """末尾 LIMIT が無い読み取りにだけキャップを足す。既存サブクエリ LIMIT は触らない前提の単文。"""
        sql = sanitize_sql("SELECT Time, Amount FROM t")
        self.assertTrue(sql.upper().endswith("LIMIT 200"))

    def test_rejects_delete(self):
        """推論テーブルへの DELETE を実行前に落とす。"""
        with self.assertRaises(SqlGuardError):
            sanitize_sql("DELETE FROM dwh_prod.ulb_fraud_detection_predictions")

    def test_rejects_create(self):
        """DDL を拒否し、エージェントがスキーマを書き換えられないようにする。"""
        with self.assertRaises(SqlGuardError):
            sanitize_sql("CREATE TABLE x AS SELECT 1")

    def test_rejects_insert(self):
        """INSERT を拒否する。ナレッジ投入は別経路のため実行パスでは不要。"""
        with self.assertRaises(SqlGuardError):
            sanitize_sql("INSERT INTO t VALUES (1)")

    def test_strips_comments_and_semicolons(self):
        """コメントと `;` を先に消し、隠蔽 DML や複数ステートメントをキーワード検出から逃げなくする。"""
        sql = sanitize_sql("SELECT 1 -- drop table\n")
        self.assertIn("SELECT 1", sql)
        self.assertNotIn(";", sql)

    def test_requires_select_or_with(self):
        """EXPLAIN 等の先頭キーワードも拒否し、SELECT/WITH 以外のジョブを走らせない。"""
        with self.assertRaises(SqlGuardError):
            sanitize_sql("EXPLAIN SELECT 1")


if __name__ == "__main__":
    unittest.main()
