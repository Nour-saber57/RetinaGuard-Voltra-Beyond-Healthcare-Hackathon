from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import Base, engine
from email_service import send_email
from routers import cases


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