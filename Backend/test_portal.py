from datetime import datetime, timezone
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from database import Base, get_db
from main import app
from routers.portal import PROVIDERS, seed_clinics


class PortalTests(unittest.TestCase):
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
        clinic = self.client.post("/auth/clinic/login", json={
            "email": "clinic@retinaguard.demo",
            "password": "DemoClinic2026!",
        }).json()
        self.clinic_headers = {"Authorization": f"Bearer {clinic['access_token']}"}

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

    def test_private_routes_require_authentication(self):
        self.assertEqual(self.client.get("/health/readings").status_code, 401)
        self.assertEqual(self.client.get("/appointments").status_code, 401)

    def test_measurements_are_persisted_and_patient_scoped(self):
        response = self.client.post("/health/readings/batch", headers=self.patient_headers, json={
            "readings": [
                {"kind": "weight", "measured_on": "2026-10-10", "value": 82.4},
                {"kind": "a1c", "measured_on": "2026-09-20", "value": 7.2},
                {"kind": "bp", "measured_on": "2026-10-12", "value": 130, "diastolic": 82},
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual([item["kind"] for item in response.json()], ["weight", "a1c", "bp"])
        other_patient = self.register("other@example.com")
        self.assertEqual(self.client.get("/health/readings", headers=other_patient).json(), [])

    def test_booking_persists_and_cancellation_releases_slot(self):
        clinic = next(item for item in PROVIDERS if item.id == "bright")
        availability = self.client.get(f"/clinics/{clinic.id}/availability").json()
        target = next(item for item in availability if item["slots"])
        slot = target["slots"][0]
        payload = {"clinic_id": clinic.id, "date": target["date"], "time": slot}
        created = self.client.post("/appointments", headers=self.patient_headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["patient_name"], "Example Patient")
        reference = created.json()["reference"]
        self.assertEqual(len(self.client.get("/appointments/mine", headers=self.patient_headers).json()), 1)
        self.assertEqual(len(self.client.get("/appointments", headers=self.clinic_headers).json()), 1)
        self.assertNotIn(slot, next(
            item["slots"] for item in self.client.get(f"/clinics/{clinic.id}/availability").json()
            if item["date"] == target["date"]
        ))
        cancelled = self.client.patch(f"/appointments/{reference}/cancel", headers=self.patient_headers)
        self.assertEqual(cancelled.json()["status"], "Cancelled")
        self.assertIn(slot, next(
            item["slots"] for item in self.client.get(f"/clinics/{clinic.id}/availability").json()
            if item["date"] == target["date"]
        ))

    def test_clinic_role_cannot_read_patient_measurements(self):
        response = self.client.get("/health/readings", headers=self.clinic_headers)
        self.assertEqual(response.status_code, 403)

    def test_progress_must_reference_owned_confirmed_booking(self):
        response = self.client.patch("/patients/me/progress", headers=self.patient_headers, json={
            "results_appointment": "RG-NOT-OWNED",
        })
        self.assertEqual(response.status_code, 400)

    def test_legacy_case_migration_preserves_rows_and_allows_unknown_age(self):
        with TemporaryDirectory() as directory:
            legacy_engine = create_engine(f"sqlite:///{directory}/legacy.db")
            with legacy_engine.begin() as connection:
                connection.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY)"))
                connection.execute(text(
                    "CREATE TABLE cases (id INTEGER PRIMARY KEY, age INTEGER NOT NULL, "
                    "diabetes_type VARCHAR(20) NOT NULL, name VARCHAR(100) NOT NULL, "
                    "email VARCHAR(254) NOT NULL, image_path VARCHAR, status VARCHAR(30) NOT NULL, "
                    "prediction VARCHAR, created_at DATETIME NOT NULL)"
                ))
                connection.execute(text(
                    "INSERT INTO cases VALUES (1, 32, 'Type 2', 'Example', 'example@example.com', "
                    "'uploads/example.png', 'image_uploaded', NULL, :created_at)"
                ), {"created_at": datetime.now(timezone.utc).isoformat()})

            with patch.object(database, "engine", legacy_engine):
                database.migrate_schema()

            columns = {column["name"]: column for column in inspect(legacy_engine).get_columns("cases")}
            self.assertTrue(columns["age"]["nullable"])
            self.assertIn("patient_id", columns)
            with legacy_engine.connect() as connection:
                record = connection.execute(text("SELECT id, age, name FROM cases")).one()
                self.assertEqual((record.id, record.age, record.name), (1, 32, "Example"))
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()