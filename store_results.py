"""
store_results.py

This module processes PDF metadata and cleanliness results
and stores them into a SQLite database called `nist_llm.db`.

Table: document_results
"""

import sqlite3
from datetime import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'nist_llm.db')

# Ensure the directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    """Initializes the SQLite database and creates the table if it doesn't exist."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                metadata_title TEXT,
                cleanliness_score REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

def store_result(file_name, metadata_title, cleanliness_score):
    """Inserts a new record into the document_results table."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO document_results (file_name, metadata_title, cleanliness_score)
            VALUES (?, ?, ?);
        """, (file_name, metadata_title, cleanliness_score))
        conn.commit()

if __name__ == "__main__":
    # Sample usage
    init_db()
    store_result("sample.pdf", "Sample Title", 95.8)
    print("✅ Sample record stored in the database.")
