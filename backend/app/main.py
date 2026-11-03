from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import models  # noqa: F401 - register SQLAlchemy models before schema creation
from .database import Base, SessionLocal, engine
from .routes import router as posts_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="SnowSocial API", version="0.1.0", lifespan=lifespan)
app.include_router(posts_router)


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
