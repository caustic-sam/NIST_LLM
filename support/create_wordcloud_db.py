# This will recreate the SQLite database with a sample schema and mock data for testing purposes.



import sqlite3
import os

db_path = os.path.join("data", "nist_llm.db")
os.makedirs("data", exist_ok=True)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Drop old table just in case
cursor.execute("DROP TABLE IF EXISTS documents")

# Create schema
cursor.execute("""
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    source TEXT,
    published TEXT
)
""")

# Insert mock data
cursor.executemany("""
INSERT INTO documents (title, source, published)
VALUES (?, ?, ?)
""", [
    ("NIST Cybersecurity Framework 2.0", "NIST", "2024-02-15"),
    ("Guide to Secure Web Applications", "NIST", "2023-11-01"),
    ("Zero Trust Architecture", "NIST", "2022-09-30")
])

conn.commit()
conn.close()

print("✅ New nist_llm.db created with sample data.")