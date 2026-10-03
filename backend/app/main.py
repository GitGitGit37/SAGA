import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Ride Along", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # surfaced to the caller, not swallowed
        db_status = f"error: {exc.__class__.__name__}"
    return {"status": "ok", "database": db_status}
