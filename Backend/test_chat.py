from datetime import date
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import HealthMeasurement, Patient
from routers import chat
from routers.portal import seed_clinics


class PatientChatTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        with self.session_factory() as session:
            seed_clinics(session)
        self.password_rounds_patch = patch("security.PASSWORD_ITERATIONS", 1000)
        self.password_rounds_patch.start()
        self.client = TestClient(app)
        self.patient_headers = self.register("patient@example.com")

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.password_rounds_patch.stop()
        self.engine.dispose()

    def register(self, email):
        result = self.client.post("/auth/register", json={
            "name": "Example Patient",
            "email": email,
            "phone": "+201012345678",
            "password": "example-password",
        }).json()
        return {"Authorization": f"Bearer {result['access_token']}"}

    def test_chat_requires_explicit_consent(self):
        with patch("routers.chat.generate_gemini_reply") as generate:
            response = self.client.post("/chat", headers=self.patient_headers, json={
                "consent": False,
                "message": "What are my readings?",
            })
        self.assertEqual(response.status_code, 400)
        generate.assert_not_called()

    def test_chat_uses_only_signed_in_patient_records(self):
        other_headers = self.register("other@example.com")
        with self.session_factory() as session:
            patient = session.scalar(select(Patient).where(Patient.email == "patient@example.com"))
            other_patient = session.scalar(select(Patient).where(Patient.email == "other@example.com"))
            session.add_all([
                HealthMeasurement(patient_id=patient.id, kind="a1c", measured_on=date(2026, 9, 1), value=8.1),
                HealthMeasurement(patient_id=other_patient.id, kind="a1c", measured_on=date(2026, 9, 1), value=5.2),
            ])
            session.commit()

        with patch("routers.chat.generate_gemini_reply", return_value="Your saved HbA1c entry is 8.1%.") as generate:
            response = self.client.post("/chat", headers=self.patient_headers, json={
                "consent": True,
                "message": "What is my latest HbA1c?",
                "history": [],
            })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("8.1", generate.call_args.args[0])
        self.assertNotIn("5.2", generate.call_args.args[0])
        self.assertNotIn("patient@example.com", generate.call_args.args[0])
        self.assertNotIn("other@example.com", generate.call_args.args[0])
        self.assertEqual(response.json()["reply"], "Your saved HbA1c entry is 8.1%.")
        self.assertEqual(self.client.get("/health/readings", headers=other_headers).status_code, 200)

    def test_gemini_request_disables_interaction_storage(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch("routers.chat.genai.Client") as client_factory:
                client_factory.return_value.interactions.create.return_value.output_text = "Grounded reply"
                result = chat.generate_gemini_reply("safe test prompt")

        self.assertEqual(result, "Grounded reply")
        client_factory.assert_called_once_with(api_key="test-key")
        call = client_factory.return_value.interactions.create.call_args.kwargs
        self.assertEqual(call["model"], "gemini-3.8-flash")
        self.assertFalse(call["store"])
        self.assertIn("not diagnose", call["system_instruction"])
        self.assertEqual(call["input"], "safe test prompt")

    def test_chat_route_requires_patient_authentication(self):
        response = self.client.post("/chat", json={
            "consent": True,
            "message": "What is in my account?",
        })
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
