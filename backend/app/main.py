from fastapi import FastAPI
from sqlalchemy import text

from app.db.session import engine

app = FastAPI(title="Cat Track", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # surfaced to the caller, not swallowed
        db_status = f"error: {exc.__class__.__name__}"
    return {"status": "ok", "database": db_status}
