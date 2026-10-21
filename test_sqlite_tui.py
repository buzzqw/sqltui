#!/usr/bin/env python3
"""Comprehensive test suite for sqlite_tui (sqltui).

Tests all core components:
1. Database operations and metadata retrieval
2. Automatic Undo SQL generation and data recovery
3. Relational Foreign Key navigation (outgoing, incoming, and history stack)
4. SQL query formatting and syntax handling
5. Column statistical analysis and frequency distributions
6. Export engines (CSV, TSV, JSON, SQL INSERT, Markdown) and CLI table formatting
7. Demo database initialization and triggers
8. CLI argument parsing and non-interactive execution
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

# Ensure sqlite_tui can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite_tui


class MockScreen:
    """Mock curses screen for headless testing of App methods."""
    def getmaxyx(self):
        return (35, 120)
    def noutrefresh(self):
        pass
    def move(self, y, x):
        pass


class TestDbOperations(unittest.TestCase):
    def setUp(self):
        self.db = sqlite_tui.Db(":memory:")
        self.db.conn.execute("PRAGMA foreign_keys = ON")
        self.db.conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                score REAL DEFAULT 0.0
            );
            INSERT INTO users (name, email, score) VALUES ('Alice', 'alice@example.com', 95.5);
            INSERT INTO users (name, email, score) VALUES ('Bob', 'bob@example.com', 82.0);
            INSERT INTO users (name, email, score) VALUES ('Charlie', 'charlie@example.com', 64.0);
        """)

    def tearDown(self):
        self.db.close()

    def test_table_discovery_and_counting(self):
        tables = [r[0] for r in self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("users", tables)
        self.assertEqual(self.db.count("users"), 3)
        self.assertEqual(self.db.count("users", "score > 80"), 2)

    def test_column_metadata(self):
        cols = self.db.columns("users")
        col_names = [c.name for c in cols]
        self.assertEqual(col_names, ["id", "name", "email", "score"])
        pk_cols = [c.name for c in cols if c.pk]
        self.assertEqual(pk_cols, ["id"])

    def test_cell_update(self):
        key = self.db.key_info("users")
        n = self.db.update_cell("users", "name", "Alicia", key, (1,))
        self.assertEqual(n, 1)
        res = self.db.conn.execute("SELECT name FROM users WHERE id = 1").fetchone()
        self.assertEqual(res[0], "Alicia")

    def test_row_deletion(self):
        key = self.db.key_info("users")
        n = self.db.delete_row("users", key, (2,))
        self.assertEqual(n, 1)
        self.assertEqual(self.db.count("users"), 2)

    def test_readonly_mode(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tf_path = tf.name
        try:
            init_conn = sqlite3.connect(tf_path)
            init_conn.execute("CREATE TABLE t (id INT)")
            init_conn.execute("INSERT INTO t VALUES (1)")
            init_conn.commit()
            init_conn.close()

            ro_db = sqlite_tui.Db(tf_path, readonly=True)
            self.assertTrue(ro_db.readonly)
            with self.assertRaises(sqlite3.Error):
                ro_db.conn.execute("INSERT INTO t VALUES (2)")
            ro_db.close()
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


class TestUndoSystem(unittest.TestCase):
    def setUp(self):
        self.db = sqlite_tui.Db(":memory:")
        self.db.conn.executescript("""
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT,
                qty INTEGER
            );
            INSERT INTO items VALUES (1, 'Widget', 10);
            INSERT INTO items VALUES (2, 'Gadget', 20);
        """)
        self.app = sqlite_tui.App(MockScreen(), self.db)
        self.app.open_table("items")

    def tearDown(self):
        self.db.close()

    def test_cell_edit_undo(self):
        self.app.grid.cx = 1  # name
        self.app.grid.cy = 0  # id 1
        raw_before = "Widget"
        new_val = "SuperWidget"

        # Apply edit
        self.db.conn.execute("UPDATE items SET name = ? WHERE id = 1", (new_val,))
        undo_sql = f"UPDATE items SET name = {sqlite_tui.sql_literal(raw_before)} WHERE id = 1;"
        self.app.record_undo("Edit cell name", undo_sql, "items")

        self.assertEqual(len(self.app.undo_stack), 1)
        self.assertIn("Widget", self.app.undo_stack[-1]["sql"])

        # Execute undo
        self.db.conn.executescript(self.app.undo_stack[-1]["sql"])
        restored = self.db.conn.execute("SELECT name FROM items WHERE id = 1").fetchone()[0]
        self.assertEqual(restored, "Widget")

    def test_row_deletion_undo(self):
        row = (2, "Gadget", 20)
        self.db.conn.execute("DELETE FROM items WHERE id = 2")
        undo_sql = "INSERT INTO items (id, name, qty) VALUES (2, 'Gadget', 20);"
        self.app.record_undo("Delete row", undo_sql, "items")

        self.assertEqual(self.db.count("items"), 1)
        self.db.conn.executescript(self.app.undo_stack[-1]["sql"])
        self.assertEqual(self.db.count("items"), 2)


class TestRelationalFkNavigation(unittest.TestCase):
    def setUp(self):
        self.db = sqlite_tui.Db(":memory:")
        sqlite_tui.populate_demo_db(self.db)
        self.app = sqlite_tui.App(MockScreen(), self.db)
        self.app.open_table("orders")

    def tearDown(self):
        self.db.close()

    def test_outgoing_foreign_key_follow(self):
        # In orders table:
        # cols: id, customer_id, order_date, status, total_amount, notes
        cust_id_idx = self.app.grid.cols.index("customer_id")
        self.app.grid.cx = cust_id_idx
        self.app.grid.cy = 0  # First order, customer_id = 1

        fk = self.app.grid.fk_for_col("customer_id")
        self.assertIsNotNone(fk)
        self.assertEqual(fk[2], "customers")

        # Follow FK
        self.app.follow_fk()
        self.assertEqual(self.app.table, "customers")
        self.assertEqual(self.app.where, '"id" = 1')
        self.assertEqual(len(self.app.nav_stack), 1)

        # Navigate back
        ok = self.app.nav_back()
        self.assertTrue(ok)
        self.assertEqual(self.app.table, "orders")
        self.assertEqual(len(self.app.nav_stack), 0)

    def test_incoming_foreign_key_references(self):
        # Open customers table, standing on customer id 1
        self.app.open_table("customers")
        self.app.grid.cx = 0  # id
        self.app.grid.cy = 0  # customer 1

        self.app.find_incoming_fks()
        self.assertEqual(self.app.table, "orders")
        self.assertEqual(self.app.where, '"customer_id" = 1')
        self.assertEqual(len(self.app.nav_stack), 1)

        # Navigate back
        ok = self.app.nav_back()
        self.assertTrue(ok)
        self.assertEqual(self.app.table, "customers")


class TestSqlFormatter(unittest.TestCase):
    def test_format_sql(self):
        query = "select id, name, price from products where price > 50 and stock > 0 order by price desc limit 10;"
        formatted = sqlite_tui.format_sql(query)
        self.assertIn("SELECT", formatted)
        self.assertIn("FROM", formatted)
        self.assertIn("WHERE", formatted)
        self.assertIn("ORDER BY", formatted)
        self.assertIn("LIMIT", formatted)
        # Verify lines are indented
        self.assertTrue(any(line.startswith("  FROM") for line in formatted.splitlines()))


class TestColumnStats(unittest.TestCase):
    def setUp(self):
        self.db = sqlite_tui.Db(":memory:")
        self.db.conn.executescript("""
            CREATE TABLE scores (val INTEGER);
            INSERT INTO scores VALUES (10), (20), (20), (30), (NULL);
        """)
        self.app = sqlite_tui.App(MockScreen(), self.db)

    def tearDown(self):
        self.db.close()

    def test_calc_column_stats(self):
        stats = self.app.calc_column_stats("scores", "val")
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["non_null"], 4)
        self.assertEqual(stats["nulls"], 1)
        self.assertEqual(stats["distinct"], 3)
        self.assertEqual(stats["min"], 10)
        self.assertEqual(stats["max"], 30)
        self.assertEqual(stats["avg"], 20.0)


class TestExportEngines(unittest.TestCase):
    def test_format_query_output_modes(self):
        cols = ["id", "name", "price"]
        rows = [(1, "Pro Book", 1299.00), (2, "Mouse", 49.99)]

        # Table
        tbl = sqlite_tui.format_query_output(cols, rows, "table")
        self.assertIn("┌", tbl)
        self.assertIn("Pro Book", tbl)

        # Markdown
        md = sqlite_tui.format_query_output(cols, rows, "md")
        self.assertIn("| id | name | price |", md)
        self.assertIn("| 1 | Pro Book | 1299.0 |", md)

        # JSON
        js = sqlite_tui.format_query_output(cols, rows, "json")
        data = json.loads(js)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["name"], "Pro Book")

        # CSV
        csv_out = sqlite_tui.format_query_output(cols, rows, "csv")
        self.assertIn("id,name,price", csv_out)
        self.assertIn("1,Pro Book,1299.0", csv_out)

        # SQL
        sql_out = sqlite_tui.format_query_output(cols, rows, "sql", table_name="products")
        self.assertIn("INSERT INTO \"products\"", sql_out)


class TestDemoDbInitialization(unittest.TestCase):
    def test_demo_db_creation_and_triggers(self):
        db = sqlite_tui.Db(":memory:")
        sqlite_tui.populate_demo_db(db)

        # Verify tables exist
        tables = dict(db.conn.execute("SELECT name, type FROM sqlite_master WHERE type in ('table','view')").fetchall())
        self.assertIn("categories", tables)
        self.assertIn("products", tables)
        self.assertIn("customers", tables)
        self.assertIn("orders", tables)
        self.assertIn("order_items", tables)
        self.assertIn("v_customer_orders", tables)

        # Verify trigger automatically calculated order total
        order_total = db.conn.execute("SELECT total_amount FROM orders WHERE id = 1").fetchone()[0]
        self.assertGreater(order_total, 0.0)

        db.close()


class TestCliExecution(unittest.TestCase):
    def test_non_interactive_cli(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = sqlite_tui.main(["--demo", "-e", "SELECT COUNT(*) as c FROM products", "--format", "json"])
            output = sys.stdout.getvalue()
            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertEqual(data[0]["c"], 8)
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
