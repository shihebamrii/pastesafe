import unittest
import os
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

# Set up test database path before imports
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_pastes.db")
os.environ["PASTE_DB_PATH"] = TEST_DB_PATH

from server import app, init_db, get_db_connection

class TestPasteSafe(unittest.TestCase):
    
    def setUp(self):
        # Initialize test DB and clear pastes table
        init_db()
        self.conn = get_db_connection()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM pastes")
        self.conn.commit()
        
        # Flask test client
        self.client = app.test_client()

    def tearDown(self):
        self.conn.close()
        # Clean up database file
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except PermissionError:
                pass

    def test_database_initialization(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("pastes", tables)

    def test_paste_storage_and_one_time_burn(self):
        # 1. Post a new encrypted secret
        payload = {
            "ciphertext": "cipher123hex",
            "iv": "iv123hex",
            "ttl": 300
        }
        response = self.client.post("/api/pastes", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.data)
        self.assertIn("paste_id", data)
        paste_id = data["paste_id"]
        
        # Verify it exists in database
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM pastes WHERE id = ?", (paste_id,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["ciphertext"], "cipher123hex")
        self.assertEqual(row["iv"], "iv123hex")
        
        # 2. Retrieve the paste (Burn on Read)
        response_get = self.client.get(f"/api/pastes/{paste_id}")
        self.assertEqual(response_get.status_code, 200)
        data_get = json.loads(response_get.data)
        self.assertEqual(data_get["ciphertext"], "cipher123hex")
        self.assertEqual(data_get["iv"], "iv123hex")
        
        # 3. Try to retrieve again (Should be burned/deleted)
        response_burned = self.client.get(f"/api/pastes/{paste_id}")
        self.assertEqual(response_burned.status_code, 404)
        
        # Verify it was physically deleted from SQLite
        cursor.execute("SELECT * FROM pastes WHERE id = ?", (paste_id,))
        self.assertIsNone(cursor.fetchone())

    def test_paste_expiration(self):
        # 1. Create a paste with 1 second expiration
        conn = get_db_connection()
        cursor = conn.cursor()
        expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()  # Already expired
        cursor.execute(
            "INSERT INTO pastes (id, ciphertext, iv, expires_at) VALUES (?, ?, ?, ?)",
            ("expired-id", "cipher", "iv", expires_at)
        )
        conn.commit()
        conn.close()
        
        # 2. Retrieve expired paste (should fail with 410 or 404)
        response = self.client.get("/api/pastes/expired-id")
        self.assertEqual(response.status_code, 410)  # Expired
        
        # Verify it is deleted
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pastes WHERE id = ?", ("expired-id",))
        self.assertIsNone(cursor.fetchone())
        conn.close()

if __name__ == "__main__":
    unittest.main()
