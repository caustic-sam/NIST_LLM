"""Tests for store_results.py using a temporary file-based SQLite database."""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import store_results  # noqa: E402


class TestStoreResults(unittest.TestCase):
    """Unit tests for init_db and store_result against a temporary DB file."""

    def setUp(self):
        """Point DB_PATH at a fresh temp file so all connections share one DB."""
        self._orig_db_path = store_results.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        store_results.DB_PATH = self._tmp.name
        store_results.init_db()
        self.conn = sqlite3.connect(self._tmp.name)

    def tearDown(self):
        """Close connection, remove temp file, and restore original DB_PATH."""
        self.conn.close()
        os.unlink(self._tmp.name)
        store_results.DB_PATH = self._orig_db_path

    def test_init_db_creates_table(self):
        """init_db should create the document_results table."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='document_results'"
        )
        self.assertIsNotNone(cursor.fetchone(), "document_results table was not created")

    def test_store_result_inserts_row(self):
        """store_result should insert a record retrievable by file_name."""
        store_results.store_result("test.pdf", "Test Title", 92.5)
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT file_name, metadata_title, cleanliness_score FROM document_results"
        )
        row = cursor.fetchone()
        self.assertIsNotNone(row, "No row found after store_result")
        self.assertEqual(row[0], "test.pdf")
        self.assertEqual(row[1], "Test Title")
        self.assertAlmostEqual(row[2], 92.5)

    def test_store_result_multiple_rows(self):
        """store_result called multiple times should insert distinct rows."""
        store_results.store_result("a.pdf", "Title A", 80.0)
        store_results.store_result("b.pdf", "Title B", 75.0)
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM document_results")
        self.assertEqual(cursor.fetchone()[0], 2)

    def test_init_db_idempotent(self):
        """Calling init_db twice should not raise or duplicate the table."""
        try:
            store_results.init_db()
        except Exception as exc:  # pylint: disable=broad-except
            self.fail(f"Second init_db() raised an exception: {exc}")


if __name__ == "__main__":
    unittest.main()
