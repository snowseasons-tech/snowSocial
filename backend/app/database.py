from collections.abc import Generator

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Settings(BaseSettings):
    database_url: str = "sqlite:///./snowsocial.db"
    worker_poll_seconds: int = Field(default=10, ge=1, le=300)
    worker_batch_size: int = Field(default=10, ge=1, le=100)
    max_delivery_attempts: int = Field(default=5, ge=1, le=20)
    claim_timeout_seconds: int = Field(default=900, ge=60, le=86400)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
