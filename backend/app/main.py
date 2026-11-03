from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .analytics import router as analytics_router
from .connections import router as connections_router
from .google_connections import router as google_connections_router
from .database import SessionLocal, settings
from .routes import router as posts_router

app = FastAPI(title="SnowSocial API", version="0.2.0")
cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Analytics-Ingest-Token"],
    )
app.include_router(posts_router)
app.include_router(connections_router)
app.include_router(google_connections_router)
app.include_router(analytics_router)


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
