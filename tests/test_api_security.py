import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import db

_test_database = tempfile.TemporaryDirectory()
os.environ["CLINICAL_SCRIBE_DB_PATH"] = os.path.join(_test_database.name, "test.db")
os.environ["CLINICAL_SCRIBE_API_KEY"] = "test-clinical-scribe-key-123"

from backend.main import REQUEST_BUCKETS, app, validate_runtime_config


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        for key in list(REQUEST_BUCKETS):
            if key.startswith("login:"):
                REQUEST_BUCKETS.pop(key, None)
        self.client = TestClient(app, base_url="https://testserver")

    def test_sensitive_routes_require_authentication(self):
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_production_configuration_requires_a_long_access_key(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "CLINICAL_SCRIBE_API_KEY": "", "API_KEY": ""},
        ):
            with self.assertRaisesRegex(RuntimeError, "required"):
                validate_runtime_config()
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "CLINICAL_SCRIBE_API_KEY": "too-short", "API_KEY": ""},
        ):
            with self.assertRaisesRegex(RuntimeError, "at least 16 characters"):
                validate_runtime_config()

    def test_live_audio_websocket_requires_authenticated_session(self):
        client = TestClient(app, base_url="http://websocket-test")
        with self.assertRaises(WebSocketDisconnect) as failure:
            with client.websocket_connect("/ws/session"):
                pass
        self.assertEqual(failure.exception.code, 1008)

        login = client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        token = client.cookies.get("clinical_scribe_session")
        self.assertTrue(token, login.headers.get("set-cookie"))
        with client.websocket_connect(
            "/ws/session",
            headers={
                "cookie": f"clinical_scribe_session={token}",
                "origin": "http://websocket-test",
                "host": "websocket-test",
            },
        ) as websocket:
            self.assertEqual(websocket.receive_json()["type"], "session_started")

    def test_login_sets_http_only_session_and_logout_revokes_it(self):
        invalid = self.client.post(
            "/api/auth/login",
            json={"api_key": "incorrect-key"},
        )
        self.assertEqual(invalid.status_code, 401)

        response = self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/api/sessions").status_code, 200)

        logout = self.client.post(
            "/api/auth/logout",
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)

    def test_cookie_authenticated_writes_reject_untrusted_origins(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        response = self.client.post(
            "/api/export-json",
            headers={"origin": "https://attacker.example"},
            json={"note": {}, "patient": {}},
        )
        self.assertEqual(response.status_code, 403)

    def test_json_export_is_versioned_and_machine_readable(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        response = self.client.post(
            "/api/export-json",
            headers={"origin": "https://testserver"},
            json={
                "note": {"subjective": ["Synthetic symptom"], "plan": []},
                "patient": {"name": "Synthetic test patient"},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schema"], "clinicalscribe.soap-note.v1")
        self.assertEqual(response.json()["note"]["subjective"], ["Synthetic symptom"])

    def test_orders_are_not_created_until_explicit_confirmation(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        saved = self.client.post(
            "/api/sessions",
            headers={"origin": "https://testserver"},
            json={
                "transcript": "Synthetic consultation",
                "patient": {"name": "Synthetic Patient"},
                "note": {"plan": ["Prescribe paracetamol 500mg"]},
            },
        )
        consultation_id = saved.json()["session_id"]
        payload = {
            "consultation_id": consultation_id,
            "note": {"plan": ["Prescribe paracetamol 500mg"]},
        }

        unconfirmed = self.client.post(
            "/api/sessions/submit",
            headers={"origin": "https://testserver"},
            json=payload,
        )
        self.assertEqual(unconfirmed.status_code, 200)
        self.assertEqual(unconfirmed.json()["pharmacy_order_ids"], [])

        confirmed = self.client.post(
            "/api/sessions/submit",
            headers={"origin": "https://testserver"},
            json={**payload, "confirm_order_routing": True},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(len(confirmed.json()["pharmacy_order_ids"]), 1)

        duplicate = self.client.post(
            "/api/sessions/submit",
            headers={"origin": "https://testserver"},
            json={**payload, "confirm_order_routing": True},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_pharmacy_orders_cannot_change_after_terminal_status(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        patient_id = db.create_patient(
            {"first_name": "Synthetic", "last_name": "Patient"}
        )
        consultation_id = db.create_consultation(patient_id)
        order_id = db.create_pharmacy_order(
            consultation_id,
            patient_id,
            "Synthetic medication",
            "500mg",
            "Twice daily",
        )
        endpoint = f"/api/pharmacy/orders/{order_id}/status"
        headers = {"origin": "https://testserver"}

        dispensed = self.client.post(
            endpoint,
            headers=headers,
            json={"status": "dispensed"},
        )
        self.assertEqual(dispensed.status_code, 200)

        cancel_dispensed = self.client.post(
            endpoint,
            headers=headers,
            json={"status": "cancelled"},
        )
        self.assertEqual(cancel_dispensed.status_code, 409)

        another_order_id = db.create_pharmacy_order(
            consultation_id,
            patient_id,
            "Another synthetic medication",
            "250mg",
            "Once daily",
        )
        another_endpoint = f"/api/pharmacy/orders/{another_order_id}/status"
        cancelled = self.client.post(
            another_endpoint,
            headers=headers,
            json={"status": "cancelled"},
        )
        self.assertEqual(cancelled.status_code, 200)

        reopen_cancelled = self.client.post(
            another_endpoint,
            headers=headers,
            json={"status": "pending"},
        )
        self.assertEqual(reopen_cancelled.status_code, 409)

    def test_lab_orders_require_a_result_and_cannot_change_after_terminal_status(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        patient_id = db.create_patient(
            {"first_name": "Synthetic", "last_name": "Patient"}
        )
        consultation_id = db.create_consultation(patient_id)
        order_id = db.create_laboratory_order(
            consultation_id, patient_id, "Synthetic CBC", "CBC", "Blood"
        )
        endpoint = f"/api/lab/orders/{order_id}/status"
        headers = {"origin": "https://testserver"}

        missing_result = self.client.post(
            endpoint,
            headers=headers,
            json={"status": "completed", "result": "   "},
        )
        self.assertEqual(missing_result.status_code, 422)

        completed = self.client.post(
            endpoint,
            headers=headers,
            json={"status": "completed", "result": "Synthetic result"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["result"], "Synthetic result")

        cancel_completed = self.client.post(
            endpoint,
            headers=headers,
            json={"status": "cancelled"},
        )
        self.assertEqual(cancel_completed.status_code, 409)

        another_order_id = db.create_laboratory_order(
            consultation_id, patient_id, "Synthetic metabolic panel"
        )
        cancel_pending = self.client.post(
            f"/api/lab/orders/{another_order_id}/status",
            headers=headers,
            json={"status": "cancelled"},
        )
        self.assertEqual(cancel_pending.status_code, 200)
        reopen_cancelled = self.client.post(
            f"/api/lab/orders/{another_order_id}/status",
            headers=headers,
            json={"status": "completed", "result": "Synthetic result"},
        )
        self.assertEqual(reopen_cancelled.status_code, 409)

    def test_saved_session_can_be_permanently_deleted(self):
        self.client.post(
            "/api/auth/login",
            json={"api_key": os.environ["CLINICAL_SCRIBE_API_KEY"]},
        )
        saved = self.client.post(
            "/api/sessions",
            headers={"origin": "https://testserver"},
            json={"transcript": "Synthetic consultation", "patient": {}, "note": {}},
        )
        session_id = saved.json()["session_id"]
        response = self.client.delete(
            f"/api/sessions/{session_id}",
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/sessions/{session_id}").status_code, 404)

    def test_login_rate_limit_is_enforced(self):
        client = TestClient(app, base_url="https://rate-limit-test")
        for _ in range(5):
            client.post("/api/auth/login", json={"api_key": "incorrect-key"})
        response = client.post("/api/auth/login", json={"api_key": "incorrect-key"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
