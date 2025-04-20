import unittest
import psycopg2
from NIST_LLM.scripts.store_results import connect_db, insert_document

class TestDatabaseOperations(unittest.TestCase):
    def setUp(self):
        """Setup a test database connection"""
        self.connection = connect_db()
        self.cursor = self.connection.cursor()

    def tearDown(self):
        """Close connection after each test"""
        self.connection.commit()
        self.cursor.close()
        self.connection.close()

    def test_connection(self):
        """Test if database connection is successful"""
        self.assertIsNotNone(self.connection, "Database connection failed!")

    def test_insert_document(self):
        """Test inserting a document"""
        test_title = "Test Document"
        test_content = "This is a test content for database storage."
        insert_document(test_title, test_content, self.connection)

        # Verify if the document is inserted
        self.cursor.execute("SELECT title, content FROM documents WHERE title = %s", (test_title,))
        result = self.cursor.fetchone()
        self.assertIsNotNone(result, "Document was not inserted.")
        self.assertEqual(result[0], test_title)
        self.assertEqual(result[1], test_content[:255])  # Ensure it respects VARCHAR limits

if __name__ == "__main__":
    unittest.main()