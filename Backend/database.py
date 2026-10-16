from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_PATH = Path(__file__).resolve().with_name("retinaflow.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def migrate_schema():
    if "cases" not in inspect(engine).get_table_names():
        return
    columns = {column["name"]: column for column in inspect(engine).get_columns("cases")}
    needs_rebuild = "patient_id" not in columns or not columns["age"]["nullable"]
    if not needs_rebuild:
        return

    with engine.begin() as connection:
        patient_id = "patient_id" if "patient_id" in columns else "NULL"
        connection.execute(text(
            "CREATE TABLE cases_new ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "age INTEGER, "
            "diabetes_type VARCHAR(20) NOT NULL, "
            "name VARCHAR(100) NOT NULL, "
            "email VARCHAR(254) NOT NULL, "
            "patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL, "
            "image_path VARCHAR, "
            "status VARCHAR(30) NOT NULL, "
            "prediction VARCHAR, "
            "created_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO cases_new "
            "(id, age, diabetes_type, name, email, patient_id, image_path, status, prediction, created_at) "
            f"SELECT id, age, diabetes_type, name, email, {patient_id}, image_path, status, prediction, created_at "
            "FROM cases"
        ))
        connection.execute(text("DROP TABLE cases"))
        connection.execute(text("ALTER TABLE cases_new RENAME TO cases"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cases_patient_id ON cases (patient_id)"
        ))


def get_db():
    with SessionLocal() as db:
        yield db