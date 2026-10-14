from contextlib import asynccontextmanager
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from database import Base, engine
from routers import cases


class Settings(BaseSettings):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


def send_email(
    recipient: str,
    subject: str,
    body: str,
):
    settings = Settings()

    message = EmailMessage()
    message["From"] = settings.smtp_user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=20,
    ) as server:
        server.starttls(context=ssl.create_default_context())

        server.login(
            settings.smtp_user,
            settings.smtp_password,
        )

        server.send_message(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="RetinaFlow API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(cases.router)


@app.get("/email")
def get_email():
    return {"email": "user@example.com"}


@app.get("/health")
def health_check():
    return {"status": "ok"}