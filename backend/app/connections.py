from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, String, UniqueConstraint, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base, get_session

router = APIRouter(prefix="/api/connections", tags=["connections"])
META_GRAPH_VERSION = os.getenv("META_GRAPH_API_VERSION", "v26.0")
META_DIALOG_URL = f"https://www.facebook.com/{META_GRAPH_VERSION}/dialog/oauth"
META_GRAPH_URL = f"https://graph.facebook.com/{META_GRAPH_VERSION}"
META_SCOPES = (
    "pages_show_list,pages_read_engagement,pages_manage_posts,"
    "instagram_basic,instagram_content_publish,instagram_manage_insights"
)
OAUTH_STATE_TTL = timedelta(minutes=10)


class ChannelConnection(Base):
    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("provider", "channel", "provider_account_id", name="uq_channel_connection_account"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_access_token: Mapped[str] = mapped_column(String(4096), nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    channel: str
    provider_account_id: str
    account_name: str
    token_expires_at: datetime | None
    created_at: datetime


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def _fernet() -> Fernet:
    key = _required_setting("TOKEN_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise HTTPException(status_code=503, detail="TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from error


def _exchange_and_discover(code: str, redirect_uri: str) -> list[dict[str, str | None]]:
    client_id = _required_setting("META_APP_ID")
    client_secret = _required_setting("META_APP_SECRET")
    timeout = httpx.Timeout(15.0, connect=5.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            token_response = client.get(
                f"{META_GRAPH_URL}/oauth/access_token",
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            token_response.raise_for_status()
            user_token = token_response.json()["access_token"]
            long_token_response = client.get(
                f"{META_GRAPH_URL}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "fb_exchange_token": user_token,
                },
            )
            long_token_response.raise_for_status()
            token_data = long_token_response.json()
            user_token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 0))
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in > 0 else None
            pages_response = client.get(
                f"{META_GRAPH_URL}/me/accounts",
                params={
                    "fields": "id,name,access_token,instagram_business_account{id,username}",
                    "access_token": user_token,
                },
            )
            pages_response.raise_for_status()
            pages = pages_response.json().get("data", [])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Meta OAuth could not complete account discovery") from error

    accounts: list[dict[str, str | None]] = []
    for page in pages:
        page_id = page.get("id")
        page_name = page.get("name")
        page_token = page.get("access_token")
        if not all(isinstance(value, str) and value for value in (page_id, page_name, page_token)):
            continue
        accounts.append(
            {
                "channel": "facebook",
                "provider_account_id": page_id,
                "account_name": page_name,
                "access_token": page_token,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )
        instagram = page.get("instagram_business_account")
        if isinstance(instagram, dict) and isinstance(instagram.get("id"), str):
            accounts.append(
                {
                    "channel": "instagram",
                    "provider_account_id": instagram["id"],
                    "account_name": instagram.get("username") or page_name,
                    "access_token": page_token,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }
            )
    return accounts


@router.get("/meta/authorize", include_in_schema=False)
def begin_meta_oauth(session: Session = Depends(get_session)) -> RedirectResponse:
    client_id = _required_setting("META_APP_ID")
    redirect_uri = _required_setting("META_REDIRECT_URI")
    # Fail before sending the user to Meta if encryption is not ready for callback storage.
    _fernet()
    state = secrets.token_urlsafe(32)
    session.add(
        OAuthState(
            state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
            provider="meta",
            expires_at=datetime.now(UTC) + OAUTH_STATE_TTL,
        )
    )
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not start Meta OAuth") from error
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": META_SCOPES,
    }
    return RedirectResponse(f"{META_DIALOG_URL}?{httpx.QueryParams(params)}", status_code=302)


@router.get("/meta/callback", include_in_schema=False)
def complete_meta_oauth(
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    oauth_state = session.scalar(select(OAuthState).where(OAuthState.state_hash == state_hash).with_for_update())
    now = datetime.now(UTC)
    expires_at = oauth_state.expires_at if oauth_state is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if oauth_state is None or oauth_state.provider != "meta" or expires_at <= now:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
    session.delete(oauth_state)
    session.commit()
    if error:
        raise HTTPException(status_code=400, detail="Meta authorization was not granted")
    if not code:
        raise HTTPException(status_code=400, detail="Meta did not return an authorization code")

    redirect_uri = _required_setting("META_REDIRECT_URI")
    accounts = _exchange_and_discover(code, redirect_uri)
    if not accounts:
        raise HTTPException(status_code=422, detail="No Facebook Pages or linked Instagram business accounts were found")
    cipher = _fernet()
    for account in accounts:
        connection = session.scalar(
            select(ChannelConnection).where(
                ChannelConnection.provider == "meta",
                ChannelConnection.channel == account["channel"],
                ChannelConnection.provider_account_id == account["provider_account_id"],
            )
        )
        if connection is None:
            connection = ChannelConnection(
                provider="meta",
                channel=str(account["channel"]),
                provider_account_id=str(account["provider_account_id"]),
            )
            session.add(connection)
        connection.account_name = str(account["account_name"])
        connection.encrypted_access_token = cipher.encrypt(str(account["access_token"]).encode("utf-8")).decode("ascii")
        expiry = account["expires_at"]
        connection.token_expires_at = datetime.fromisoformat(expiry) if isinstance(expiry, str) else None
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not save Meta account connections") from error
    frontend_url = os.getenv("META_FRONTEND_URL", "http://localhost:8080").rstrip("/")
    return RedirectResponse(f"{frontend_url}/?connection=meta", status_code=303)


@router.get("", response_model=list[ConnectionRead])
def list_connections(session: Session = Depends(get_session)) -> list[ChannelConnection]:
    return list(session.scalars(select(ChannelConnection).order_by(ChannelConnection.channel, ChannelConnection.account_name)))


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(connection_id: UUID, session: Session = Depends(get_session)) -> None:
    connection = session.get(ChannelConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    session.delete(connection)
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not disconnect channel") from error


def decrypt_access_token(connection: ChannelConnection) -> str:
    try:
        return _fernet().decrypt(connection.encrypted_access_token.encode("ascii")).decode("utf-8")
    except HTTPException as error:
        raise ValueError(str(error.detail)) from error
    except (InvalidToken, UnicodeEncodeError) as error:
        raise ValueError("Stored channel token could not be decrypted") from error
