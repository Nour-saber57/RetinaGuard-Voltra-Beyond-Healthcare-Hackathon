from io import BytesIO
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import Case
from routers import cases


class CaseUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory()
        self.upload_directory = Path(self.temp_directory.name) / "uploads"
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        def override_get_db():
            with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.upload_directory_patch = patch.object(cases, "UPLOAD_DIR", self.upload_directory)
        self.upload_directory_patch.start()
        self.email_patch = patch.object(cases, "send_email")
        self.email_sender = self.email_patch.start()
        self.email_setting_patch = patch.dict(os.environ, {"SEND_CASE_EMAILS": "false"})
        self.email_setting_patch.start()
        self.password_rounds_patch = patch("security.PASSWORD_ITERATIONS", 1000)
        self.password_rounds_patch.start()
        self.client = TestClient(app)
        account = self.client.post("/auth/register", json={
            "name": "Alex Example",
            "email": "alex@example.com",
            "phone": "+201012345678",
            "password": "test-password-123",
        }).json()
        self.patient_id = account["account"]["id"]
        self.patient_headers = {"Authorization": f"Bearer {account['access_token']}"}
        clinic = self.client.post("/auth/clinic/login", json={
            "email": "clinic@retinaguard.demo",
            "password": "DemoClinic2026!",
        }).json()
        self.clinic_headers = {"Authorization": f"Bearer {clinic['access_token']}"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.email_patch.stop()
        self.email_setting_patch.stop()
        self.password_rounds_patch.stop()
        self.upload_directory_patch.stop()
        self.engine.dispose()
        self.temp_directory.cleanup()

    @staticmethod
    def valid_form():
        return {
            "name": "Alex Example",
            "age": "32",
            "email": "alex@example.com",
            "diabetes_type": "Type 2",
        }

    @staticmethod
    def png_bytes():
        image = BytesIO()
        Image.new("RGB", (32, 32), color="red").save(image, format="PNG")
        return image.getvalue()

    def submit(self, form=None, image=None, filename="client-name.png", content_type="image/png"):
        return self.client.post(
            "/cases",
            data=form or self.valid_form(),
            files={"image": (filename, self.png_bytes() if image is None else image, content_type)},
            headers=self.patient_headers,
        )

    def case_count(self):
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(Case))

    def create_saved_case(self):
        with self.session_factory() as session:
            case = Case(
                name="Alex Example",
                age=32,
                email="alex@example.com",
                diabetes_type="Type 2",
                image_path="uploads/example.png",
                status="image_uploaded",
                prediction=None,
                patient_id=self.patient_id,
            )
            session.add(case)
            session.commit()
            return case.id

    def assert_no_saved_files(self):
        files = list(self.upload_directory.iterdir()) if self.upload_directory.exists() else []
        self.assertEqual(files, [])

    def test_valid_upload_saves_case_and_uses_generated_path(self):
        response = self.submit()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "image_uploaded")
        self.assertIsNone(response.json()["prediction"])
        self.assertEqual(self.case_count(), 1)
        self.email_sender.assert_not_called()

        with self.session_factory() as session:
            case = session.scalar(select(Case))
            self.assertEqual(case.id, response.json()["id"])
            self.assertEqual(case.name, "Alex Example")
            self.assertEqual(case.age, 32)
            self.assertEqual(case.diabetes_type, "Not specified")
            self.assertTrue(case.image_path.startswith("uploads/"))
            stored_image = self.upload_directory / Path(case.image_path).name
            self.assertTrue(stored_image.is_file())
            self.assertNotEqual(stored_image.name, "client-name.png")

    def test_invalid_email_rejected_on_registration(self):
        response = self.client.post("/auth/register", json={
            "name": "Alex Example",
            "email": "not-an-email",
            "phone": "+201012345678",
            "password": "test-password-123",
        })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_damaged_image_saves_nothing(self):
        response = self.submit(image=b"not an image", filename="broken.jpg", content_type="image/jpeg")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_empty_image_saves_nothing(self):
        response = self.submit(image=b"")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_unsupported_image_format_saves_nothing(self):
        image = BytesIO()
        Image.new("RGB", (8, 8)).save(image, format="GIF")

        response = self.submit(image=image.getvalue(), filename="image.gif", content_type="image/gif")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_oversized_image_saves_nothing(self):
        response = self.submit(image=b"x" * (10 * 1024 * 1024 + 1))

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_image_over_pixel_limit_saves_nothing(self):
        image_header = bytearray(self.png_bytes())
        ihdr_data = struct.pack(">II", 7000, 7000) + image_header[24:29]
        image_header[16:29] = ihdr_data
        image_header[29:33] = struct.pack(
            ">I",
            zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF,
        )

        response = self.submit(image=bytes(image_header))

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_database_failure_removes_written_image(self):
        with patch.object(Session, "commit", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                self.submit()

        self.assertEqual(self.case_count(), 0)
        self.assert_no_saved_files()

    def test_file_save_failure_saves_no_case(self):
        self.upload_directory.write_text("not a directory")

        with self.assertRaises(OSError):
            self.submit()

        self.assertEqual(self.case_count(), 0)
        self.assertTrue(self.upload_directory.is_file())

    def test_email_failure_does_not_fail_saved_case(self):
        self.email_sender.side_effect = RuntimeError("SMTP unavailable")

        with patch.dict(os.environ, {"SEND_CASE_EMAILS": "true"}):
            with self.assertLogs("routers.cases", level="ERROR") as captured:
                response = self.submit()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.case_count(), 1)
        self.assertEqual(len(list(self.upload_directory.iterdir())), 1)
        log_output = "\n".join(captured.output)
        self.assertNotIn("Alex Example", log_output)
        self.assertNotIn("alex@example.com", log_output)

    def test_get_cases_returns_saved_database_rows(self):
        case_id = self.create_saved_case()

        response = self.client.get("/cases", headers=self.clinic_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["id"], case_id)
        self.assertEqual(response.json()[0]["name"], "Alex Example")
        self.assertEqual(response.json()[0]["diabetes_type"], "Type 2")
        self.assertEqual(response.json()[0]["status"], "image_uploaded")

    def test_get_case_returns_saved_row_by_id(self):
        case_id = self.create_saved_case()

        response = self.client.get(f"/cases/{case_id}", headers=self.patient_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], case_id)
        self.assertEqual(response.json()["email"], "alex@example.com")

    def test_get_case_returns_404_for_unknown_id(self):
        response = self.client.get("/cases/999", headers=self.patient_headers)

        self.assertEqual(response.status_code, 404)

    def test_patient_case_list_is_private(self):
        case_id = self.create_saved_case()
        other_account = self.client.post("/auth/register", json={
            "name": "Other Patient",
            "email": "other@example.com",
            "phone": "+201012345679",
            "password": "test-password-456",
        }).json()
        other_headers = {"Authorization": f"Bearer {other_account['access_token']}"}

        self.assertEqual(self.client.get("/cases/mine", headers=self.patient_headers).json()[0]["id"], case_id)
        self.assertEqual(self.client.get("/cases/mine", headers=other_headers).json(), [])


if __name__ == "__main__":
    unittest.main()