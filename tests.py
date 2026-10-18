"""Regression tests for TrueDose core workflows. Run: python -m unittest -v tests.py"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TRUEDOSE_ADMIN_PASSWORD", "test-password-for-suite")
os.environ.setdefault("TRUEDOSE_SECRET_KEY", "test-secret-key-only-for-tests")
import app as td


class TrueDoseSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        td.DB_PATH = Path(self.tmp.name) / "test.db"
        td.init_db()
        td.app.config.update(TESTING=True, SECRET_KEY="unit-test-secret")
        self.client = td.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def token(self, path):
        self.client.get(path)
        with self.client.session_transaction() as sess:
            return sess["csrf_token"]

    def add_medicine(self, code="TD-TEST-1", manufacturer="Test manufacturer"):
        with td.connect_db() as conn:
            conn.execute(
                "INSERT INTO medicines(code,name,manufacturer,batch,expiry,status) VALUES(?,?,?,?,?,?)",
                (code, "Test medicine 500 mg", manufacturer, "LOT-1", "2030-01-01", "registered"),
            )
            conn.commit()

    def test_health_and_empty_registry(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(self.client.get("/api/medicines").json, [])

    def test_known_and_unknown_code_and_ledger(self):
        self.add_medicine()
        response = self.client.post("/api/verify", json={"code": " td-test-1 "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["result"], "REGISTERED RECORD")
        self.assertIn("does not prove", response.json["disclaimer"])
        unknown = self.client.post("/api/verify", json={"code": "TD-NOT-FOUND"})
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json["result"], "UNKNOWN CODE")
        self.assertTrue(self.client.get("/api/ledger/status").json["valid"])

    def test_admin_login_csrf_and_create(self):
        token = self.token("/admin/login")
        bad = self.client.post("/admin/login", data={"password": os.environ["TRUEDOSE_ADMIN_PASSWORD"]})
        self.assertEqual(bad.status_code, 400)
        response = self.client.post("/admin/login", data={"password": os.environ["TRUEDOSE_ADMIN_PASSWORD"], "csrf_token": token}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Registry management", response.data)
        with self.client.session_transaction() as sess:
            csrf = sess["csrf_token"]
        response = self.client.post("/admin/medicines", data={
            "csrf_token": csrf, "code": "TD-TEST-9", "name": "Test tablet 10 mg",
            "manufacturer": "Test manufacturer", "batch": "LOT-9", "expiry": "2030-01-01", "status": "registered",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(row["code"] == "TD-TEST-9" for row in self.client.get("/api/medicines").json))

    def test_customer_register_and_login_csrf(self):
        token = self.token("/register")
        response = self.client.post("/register", data={
            "csrf_token": token, "username": "testcustomer", "password": "correct-horse-battery-staple",
            "role": "customer", "organization": "",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign in to your workspace", response.data)
        token = self.token("/login")
        response = self.client.post("/login", data={
            "csrf_token": token, "username": "testcustomer", "password": "correct-horse-battery-staple",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Customer verification", response.data)

    def test_business_role_requires_approval_and_manufacturer_scope(self):
        token = self.token("/register")
        response = self.client.post("/register", data={
            "csrf_token": token, "username": "makeruser", "password": "correct-horse-battery-staple",
            "role": "manufacturer", "organization": "Test manufacturer",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        token = self.token("/login")
        response = self.client.post("/login", data={
            "csrf_token": token, "username": "makeruser", "password": "correct-horse-battery-staple",
        }, follow_redirects=True)
        self.assertIn(b"awaiting administrator approval", response.data)

    def test_ledger_tampering_detected(self):
        td.append_ledger_event("test_event", "test", "TD-X", {"value": 1})
        with td.connect_db() as conn:
            conn.execute("UPDATE ledger SET payload='{}' WHERE event_type='test_event'")
            conn.commit()
        self.assertFalse(td.verify_ledger_chain()["valid"])

    def test_history_and_ledger_pages_and_export(self):
        self.add_medicine()
        self.client.post("/api/verify", json={"code": "TD-TEST-1"})
        history = self.client.get("/history?q=TD-TEST-1")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"Verification history", history.data)
        self.assertIn(b"TD-TEST-1", history.data)
        export = self.client.get("/history/export.csv?q=TD-TEST-1")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"TD-TEST-1", export.data)
        ledger = self.client.get("/ledger")
        self.assertEqual(ledger.status_code, 200)
        self.assertIn(b"Ledger integrity status", ledger.data)
        self.assertEqual(self.client.get("/ledger/export.json").status_code, 302)

    def test_admin_audit_history_requires_admin(self):
        self.assertEqual(self.client.get("/admin/audit-history").status_code, 302)
        token = self.token("/admin/login")
        response = self.client.post("/admin/login", data={
            "password": os.environ["TRUEDOSE_ADMIN_PASSWORD"], "csrf_token": token,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/admin/audit-history").status_code, 200)
        self.assertEqual(self.client.get("/admin/audit-history/export.csv").status_code, 200)

    def test_clear_history_keeps_ledger_event(self):
        self.add_medicine()
        self.client.post("/api/verify", json={"code": "TD-TEST-1"})
        token = self.token("/admin/login")
        self.client.post("/admin/login", data={"password": os.environ["TRUEDOSE_ADMIN_PASSWORD"], "csrf_token": token})
        with self.client.session_transaction() as sess:
            csrf = sess["csrf_token"]
        response = self.client.post("/api/reset-history", headers={"X-CSRF-Token": csrf})
        # The endpoint currently reads the CSRF token from form data, so submit form data.
        if response.status_code == 400:
            response = self.client.post("/api/reset-history", data={"csrf_token": csrf})
        self.assertEqual(response.status_code, 200)
        with td.connect_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0], 0)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM ledger WHERE event_type='scan_history_cleared'").fetchone()[0], 0)

    def test_ipcam_rejects_loopback(self):
        response = self.client.get("/api/ipcam/stream?url=http://127.0.0.1:8080/video")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
