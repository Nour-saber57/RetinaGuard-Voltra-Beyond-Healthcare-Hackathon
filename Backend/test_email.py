from main import send_email


if __name__ == "__main__":
    send_email(
        recipient="noormohmed584@gmail.com",
        subject="RetinaFlow test",
        body="Hello! This is a test email from RetinaFlow.",
    )

    print("Email accepted by the SMTP server.")