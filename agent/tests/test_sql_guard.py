"""Unit tests for SQL sanitization."""

import unittest

from agent.sql_guard import SqlGuardError, sanitize_sql


class SqlGuardTest(unittest.TestCase):
    def test_select_gets_limit(self):
        sql = sanitize_sql("SELECT Time, Amount FROM t")
        self.assertTrue(sql.upper().endswith("LIMIT 200"))

    def test_rejects_delete(self):
        with self.assertRaises(SqlGuardError):
            sanitize_sql("DELETE FROM dwh_prod.ulb_fraud_detection_predictions")

    def test_rejects_create(self):
        with self.assertRaises(SqlGuardError):
            sanitize_sql("CREATE TABLE x AS SELECT 1")

    def test_rejects_insert(self):
        with self.assertRaises(SqlGuardError):
            sanitize_sql("INSERT INTO t VALUES (1)")

    def test_strips_comments_and_semicolons(self):
        sql = sanitize_sql("SELECT 1 -- drop table\n")
        self.assertIn("SELECT 1", sql)
        self.assertNotIn(";", sql)

    def test_requires_select_or_with(self):
        with self.assertRaises(SqlGuardError):
            sanitize_sql("EXPLAIN SELECT 1")


if __name__ == "__main__":
    unittest.main()
