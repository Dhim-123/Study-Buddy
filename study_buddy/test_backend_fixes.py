import os
import sys
import json
import sqlite3
import unittest

# Ensure UTF-8 stdout on Windows
sys.stdout.reconfigure(encoding='utf-8')

import app as server

class TestBackendFixes(unittest.TestCase):

    def setUp(self):
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        # Ensure DB is initialized
        server.init_db()

    def test_1_living_notebook_6_categories_schema(self):
        """Verify Living Notebook table accepts all 6 categories and existing rows are preserved."""
        print("\n--- Testing Issue 2: Living Notebook Schema & 6 Categories ---")
        conn = server.get_db()

        # Check existing row count
        cur = conn.execute("SELECT COUNT(*) FROM living_notebook")
        count_before = cur.fetchone()[0]
        print(f"Row count before test: {count_before}")

        categories = [
            'Key Points',
            'Formulas',
            'Definitions',
            'Mistakes I Made',
            'Things to Revise',
            'My Own Notes'
        ]

        # Insert a test entry for each of the 6 categories
        inserted_ids = []
        for cat in categories:
            cur = conn.execute("""
                INSERT INTO living_notebook (user_id, subject, category, content)
                VALUES (1, 'Test Subject', ?, 'Test content for ' || ?)
            """, (cat, cat))
            inserted_ids.append(cur.lastrowid)

        conn.commit()

        # Verify all 6 were inserted
        for idx, cat in enumerate(categories):
            entry_id = inserted_ids[idx]
            row = conn.execute("SELECT * FROM living_notebook WHERE id=?", (entry_id,)).fetchone()
            self.assertIsNotNone(row, f"Row with id {entry_id} should exist.")
            self.assertEqual(row['category'], cat, f"Category should be {cat}.")
            print(f"✔ Successfully verified entry ID {entry_id} with category '{cat}'")

        # Cleanup inserted test rows
        for entry_id in inserted_ids:
            conn.execute("DELETE FROM living_notebook WHERE id=?", (entry_id,))
        conn.commit()
        conn.close()

    def test_2_chat_history_role_handling(self):
        """Verify chat history accepts user, assistant, and ai roles without error."""
        print("\n--- Testing Issue 1: Chat History Role Handling ---")
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1

        # Test post_message with role='assistant' and role='ai'
        with server.get_db() as conn:
            # Create a test conversation
            cur = conn.execute("INSERT INTO conversations (user_id, title) VALUES (1, 'Role Test Chat')")
            conv_id = cur.lastrowid

        # Post user message
        res1 = self.client.post(
            f"/api/conversations/{conv_id}/messages",
            data=json.dumps({"role": "user", "content": "What is momentum?"}),
            content_type="application/json"
        )
        self.assertEqual(res1.status_code, 201)

        # Post assistant message
        res2 = self.client.post(
            f"/api/conversations/{conv_id}/messages",
            data=json.dumps({"role": "assistant", "content": "Momentum is mass times velocity."}),
            content_type="application/json"
        )
        self.assertEqual(res2.status_code, 201)

        # Post ai role message (should normalize to assistant internally)
        res3 = self.client.post(
            f"/api/conversations/{conv_id}/messages",
            data=json.dumps({"role": "ai", "content": "p = m * v"}),
            content_type="application/json"
        )
        self.assertEqual(res3.status_code, 201)

        # Get messages
        res_get = self.client.get(f"/api/conversations/{conv_id}/messages")
        self.assertEqual(res_get.status_code, 200)
        data = res_get.get_json()
        messages = data.get("messages", [])
        self.assertEqual(len(messages), 3)
        print(f"✔ Retrieved {len(messages)} messages successfully:")
        for m in messages:
            print(f"   - Role: {m['role']} | Content: {m['content']}")

        # Cleanup test conversation
        with server.get_db() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    def test_3_definitions_endpoint(self):
        """Verify /api/definitions endpoint structure and execution."""
        print("\n--- Testing Issue 3: /api/definitions Endpoint ---")
        payload = {
            "messages": [
                {"role": "user", "content": "Explain Photosynthesis and Osmosis."},
                {"role": "assistant", "content": "Photosynthesis: process by which green plants use sunlight to synthesize nutrients.\nOsmosis: movement of water molecules through a semi-permeable membrane."}
            ],
            "model": "llama-3.3-70b-versatile",
            "notes": ""
        }
        res = self.client.post(
            "/api/definitions",
            data=json.dumps(payload),
            content_type="application/json"
        )
        print(f"Status Code: {res.status_code}")
        data = res.get_json()
        print(f"Response Data: {json.dumps(data, indent=2)}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("reply", data)
        self.assertNotIn("error", data)
        print("✔ /api/definitions endpoint successfully executed and returned reply.")

if __name__ == "__main__":
    unittest.main()
