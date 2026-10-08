import unittest
from unittest.mock import patch, MagicMock

from leanreview.db import (connection_config, validate_select, inspect_schema,
                           explain, summarize_plan, db_action, open_readonly)


class ConfigTests(unittest.TestCase):
    def test_remote_requires_tls(self):
        with self.assertRaises(ValueError):
            connection_config("postgresql://alice:pw@db.example/acme?sslmode=disable")
        cfg = connection_config("postgresql://alice:p%40ss@db.example/acme")
        self.assertEqual(cfg["sslmode"], "verify-full")
        self.assertEqual(cfg["password"], "p@ss")

    def test_mysql_local(self):
        cfg = connection_config("mysql://u:p@127.0.0.1:3307/a")
        self.assertEqual((cfg["dialect"], cfg["port"], cfg["sslmode"]), ("mysql", 3307, "disable"))

    def test_reject_unsafe_queries(self):
        bad = ["DELETE FROM users", "WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x",
               "SELECT 1; DROP TABLE a", "SELECT * FROM users FOR UPDATE", "SELECT pg_sleep(2)",
               "SELECT * INTO OUTFILE '/tmp/a' FROM users", "SELECT pg_advisory_xact_lock(1)",
               "SELECT 1 -- comment"]
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_select(query)

    def test_accept_select_literals(self):
        self.assertEqual(validate_select("SELECT 'FOR UPDATE', id FROM orders LIMIT 1"),
                         "SELECT 'FOR UPDATE', id FROM orders LIMIT 1")


class PlanTests(unittest.TestCase):
    def test_pg_plan_summary_does_not_leak_filters(self):
        plan = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "orders",
                          "Plan Rows": 100, "Actual Rows": 1, "Filter": "email = 'private@example.com'"}}]
        data = summarize_plan("postgres", plan, analyze=True)
        self.assertEqual(data["findings"][0]["rule"], "sequential-scan")
        self.assertEqual(data["findings"][1]["factor"], 100.0)
        self.assertNotIn("private@example.com", str(data))

    def test_mysql_plan_findings(self):
        data = summarize_plan("mysql", '{"query_block":{"table":{"table_name":"users","access_type":"ALL"}}}', False)
        self.assertEqual(data["findings"][0]["rule"], "full-scan")

    def test_query_embedded_in_explain_only(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"query_block":{"table":{"table_name":"a","access_type":"ref"}}}',)]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        result = explain(conn, "mysql", "SELECT * FROM a WHERE id=1")
        self.assertEqual(result["engine"], "mysql")
        self.assertNotIn("SELECT *", str(result))
        self.assertTrue(cursor.execute.call_args.args[0].startswith("EXPLAIN FORMAT=JSON SELECT"))

    def test_schema_filtered_before_limit(self):
        from leanreview.db import inspect_schema
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.description = [(n,) for n in ("schema_name", "table_name")]
        cur.fetchall.return_value = [("public", "late_table")]
        report = inspect_schema(conn, "postgres", 2, ["late_table"])
        self.assertEqual(len(report["tables"]), 1)
        self.assertEqual(len(report["indexes"]), 1)
        self.assertIn("WHERE table_name IN (%s)", cur.execute.call_args.args[0])

    def test_db_action_rejects_unsafe_before_connect(self):
        with patch("leanreview.db.open_readonly") as connect:
            with self.assertRaises(ValueError):
                db_action("explain", url="mysql://u:p@localhost/a", sql="DROP TABLE a")
            connect.assert_not_called()

    def test_readonly_postgres_rollback(self):
        import sys, types
        pg = types.SimpleNamespace()
        conn = MagicMock()
        pg.connect = MagicMock(return_value=conn)
        cfg = connection_config("postgresql://u:p@localhost/test")
        with patch.dict(sys.modules, {"psycopg": pg}):
            with open_readonly(cfg) as handle:
                self.assertIs(handle, conn)
        commands = [x.args[0] for x in conn.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertIn("BEGIN READ ONLY", commands)
        self.assertIn("ROLLBACK", commands)
        conn.close.assert_called_once()

    def test_readonly_mysql_rollback(self):
        import sys, types
        py = types.SimpleNamespace()
        conn = MagicMock()
        py.connect = MagicMock(return_value=conn)
        cfg = connection_config("mysql://u:p@localhost/test")
        with patch.dict(sys.modules, {"pymysql": py}):
            with open_readonly(cfg):
                pass
        commands = [x.args[0] for x in conn.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertIn("START TRANSACTION READ ONLY", commands)
        self.assertIn("ROLLBACK", commands)
        conn.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
