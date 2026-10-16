import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from models import Patient

TOKEN_SECRET = os.environ.get("RETINAGUARD_SECRET_KEY") or secrets.token_urlsafe(32)
TOKEN_TTL_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 310_000
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    role: str
    subject: str
    patient: Patient | None = None


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def issue_token(subject: str, role: str) -> str:
    payload = _encode(json.dumps({
        "sub": subject,
        "role": role,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }, separators=(",", ":")).encode())
    signature = _encode(hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_token(token: str) -> dict:
    try:
        payload, signature = token.split(".", 1)
        expected = _encode(hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["exp"] <= int(time.time()) or data["role"] not in {"patient", "clinic"}:
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError, OverflowError, binascii.Error, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.urlsafe_b64decode(salt + "=" * (-len(salt) % 4)),
            int(iterations),
        )
        return hmac.compare_digest(_encode(digest), expected)
    except (ValueError, TypeError):
        return False


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = read_token(credentials.credentials)
    patient = None
    if token["role"] == "patient":
        patient = db.get(Patient, int(token["sub"]))
        if patient is None:
            raise HTTPException(status_code=401, detail="Patient account no longer exists")
    return Principal(role=token["role"], subject=token["sub"], patient=patient)


def require_patient(principal: Principal = Depends(current_principal)) -> Patient:
    if principal.role != "patient" or principal.patient is None:
        raise HTTPException(status_code=403, detail="Patient access required")
    return principal.patient


def require_clinic(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "clinic":
        raise HTTPException(status_code=403, detail="Clinic access required")
    return principal