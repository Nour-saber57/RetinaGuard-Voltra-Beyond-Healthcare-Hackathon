from contextlib import asynccontextmanager
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine, migrate_schema, SessionLocal
from email_service import send_email
from routers import cases, portal


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    with SessionLocal() as db:
        portal.seed_clinics(db)
    yield


app = FastAPI(
    title="RetinaGuard API",
    version="0.1.0",
    lifespan=lifespan,
)

configured_origins = os.getenv("FRONTEND_ORIGIN", "").split(",")
origins = {origin.strip() for origin in configured_origins if origin.strip()}
origins.update({"http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173"})
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(cases.router)
app.include_router(portal.router)


@app.get("/email")
def get_email():
    return {"email": "user@example.com"}


@app.get("/health")
def health_check():
    return {"status": "ok"}