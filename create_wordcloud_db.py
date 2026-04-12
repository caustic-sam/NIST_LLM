"""Bootstrap the SQLite database with the documents schema and sample seed data."""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nist_llm.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Create schema only if it doesn't already exist — safe to re-run
cursor.execute("""
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    source TEXT,
    published TEXT
)
""")

# Insert seed data only when the table is empty
cursor.execute("SELECT COUNT(*) FROM documents")
if cursor.fetchone()[0] == 0:
    cursor.executemany(
        "INSERT INTO documents (title, source, published) VALUES (?, ?, ?)",
        [
            ("NIST Cybersecurity Framework 2.0", "NIST", "2024-02-15"),
            ("Guide to Secure Web Applications", "NIST", "2023-11-01"),
            ("Zero Trust Architecture", "NIST", "2022-09-30"),
        ],
    )
    print("Seed data inserted.")
else:
    print("documents table already has data — skipping seed insert.")

conn.commit()
conn.close()
print(f"Database ready: {DB_PATH}")

