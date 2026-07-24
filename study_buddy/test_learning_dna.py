import os
import sys
import json
import sqlite3
import unittest

sys.stdout.reconfigure(encoding='utf-8')

import app as server

class TestLearningDNA(unittest.TestCase):

    def setUp(self):
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        server.init_db()

    def test_learning_dna_flow(self):
        print("\n--- Testing Learning DNA Endpoints & Tracking ---")

        TEST_UID = 9999
        # Cleanup any existing test data for test user
        with server.get_db() as conn:
            conn.execute("DELETE FROM learning_dna WHERE user_id=?", (TEST_UID,))
            conn.execute("DELETE FROM subject_analytics WHERE user_id=?", (TEST_UID,))
            conn.execute("DELETE FROM student_mistakes WHERE user_id=?", (TEST_UID,))
            conn.execute("INSERT OR IGNORE INTO users (id, identifier, password_hash) VALUES (?, 'test_dna_user', 'hash')", (TEST_UID,))

        # Simulate logged in test user
        with self.client.session_transaction() as sess:
            sess['user_id'] = TEST_UID

        # 1. Fetch initial Learning DNA profile
        res1 = self.client.get("/api/learning_dna")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        print("Initial Learning DNA Profile:")
        print(json.dumps(data1, indent=2))
        self.assertIn("totalStudyMinutes", data1)
        self.assertIn("accuracy", data1)
        self.assertIn("subjectBreakdown", data1)

        # 2. Track activity (study time & quiz results)
        track_payload = {
            "studyMinutes": 15,
            "subject": "Physics",
            "quizResult": {
                "subject": "Physics",
                "questionsTaken": 10,
                "questionsCorrect": 9
            },
            "preferredStyle": "Step-by-Step",
            "learningPace": "Fast",
            "mistake": "Failed to convert cm to meters in kinematics formula"
        }

        res_track1 = self.client.post(
            "/api/learning_dna/track",
            data=json.dumps(track_payload),
            content_type="application/json"
        )
        self.assertEqual(res_track1.status_code, 200)
        self.assertTrue(res_track1.get_json().get("ok"))

        # Track second subject (Mathematics - lower accuracy)
        track_payload2 = {
            "studyMinutes": 25,
            "subject": "Mathematics",
            "quizResult": {
                "subject": "Mathematics",
                "questionsTaken": 10,
                "questionsCorrect": 5
            }
        }
        self.client.post("/api/learning_dna/track", data=json.dumps(track_payload2), content_type="application/json")

        # 3. Fetch updated Learning DNA profile
        res2 = self.client.get("/api/learning_dna")
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        print("\nUpdated Learning DNA Profile:")
        print(json.dumps(data2, indent=2))

        self.assertGreaterEqual(data2["totalStudyMinutes"], 40)
        self.assertEqual(data2["totalQuestions"], 20)
        self.assertEqual(data2["correctQuestions"], 14)
        self.assertEqual(data2["accuracy"], 70.0) # 14/20 = 70%
        self.assertIn("Physics", data2["strongestSubjects"])
        self.assertIn("Mathematics", data2["weakestSubjects"])
        self.assertEqual(len(data2["commonMistakes"]), 1)
        self.assertIn("topicsMastered", data2)
        self.assertIn("topicsToRevise", data2)
        self.assertIn("buddyAdvice", data2)
        self.assertIn("recentProgress", data2)
        self.assertTrue(len(data2["topicsMastered"]) > 0 or len(data2["topicsToRevise"]) > 0)
        print("✔ Learning DNA tracking, accuracy calculations, 8 dashboard cards metrics, and subject analysis verified successfully!")

        # Cleanup test user data
        with server.get_db() as conn:
            conn.execute("DELETE FROM learning_dna WHERE user_id=?", (TEST_UID,))
            conn.execute("DELETE FROM subject_analytics WHERE user_id=?", (TEST_UID,))
            conn.execute("DELETE FROM student_mistakes WHERE user_id=?", (TEST_UID,))
            conn.execute("DELETE FROM users WHERE id=?", (TEST_UID,))

if __name__ == "__main__":
    unittest.main()
