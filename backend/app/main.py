from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .connections import router as connections_router
from .database import SessionLocal
from .routes import router as posts_router

app = FastAPI(title="SnowSocial API", version="0.2.0")
app.include_router(posts_router)
app.include_router(connections_router)


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
