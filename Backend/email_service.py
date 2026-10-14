from pathlib import Path
import smtplib
import ssl
from email.message import EmailMessage

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore",
    )


def send_email(recipient: str, subject: str, body: str) -> None:
    settings = Settings()

    message = EmailMessage()
    message["From"] = settings.smtp_user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL(
        settings.smtp_host,
        settings.smtp_port,
        context=ssl.create_default_context(),
        timeout=30,
    ) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)