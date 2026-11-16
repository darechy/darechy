"""Версионированный API для сервисов и Telegram-бота."""

import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

from app.api.admin_routes import require_admin
from app.api.routes import (
    answer,
    get_service,
    get_situation,
    list_services,
    list_situations,
    synthesize,
    transcribe,
)
from app.core.config import Settings, get_settings
from app.models.schemas import ChatRequest, ChatResponse, Service, Situation
from app.services.api_keys import ApiKeyInfo, ApiKeyStore, CreatedApiKey

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def get_key_store(settings: Settings = Depends(get_settings)) -> ApiKeyStore:
    return ApiKeyStore(settings.api_keys_db)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    store: ApiKeyStore = Depends(get_key_store),
) -> str:
    try:
        key_id = store.resolve(credentials.credentials) if credentials else None
    except (sqlite3.Error, OSError) as exc:
        logger.exception("Хранилище ключей интеграций недоступно")
        raise HTTPException(status_code=503, detail="Проверка API-ключа недоступна") from exc
    if not key_id:
        raise HTTPException(
            status_code=401,
            detail="Требуется действующий API-ключ",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return key_id


integration_router = APIRouter()
versioned = APIRouter(
    prefix="/api/v1", tags=["integration"], dependencies=[Depends(require_api_key)]
)


class IntegrationChatRequest(ChatRequest):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    service_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


class SituationWithServices(Situation):
    services: list[Service]


class Transcription(BaseModel):
    text: str


@versioned.post("/chat", response_model=ChatResponse)
def chat(
    body: IntegrationChatRequest,
    request: Request,
    key_id: str = Depends(require_api_key),
) -> ChatResponse:
    return answer(body, request, namespace=f"integration:{key_id}")


versioned.add_api_route("/services", list_services, methods=["GET"], response_model=list[Service])
versioned.add_api_route("/services/{service_id}", get_service, methods=["GET"], response_model=Service)
versioned.add_api_route("/situations", list_situations, methods=["GET"], response_model=list[Situation])
versioned.add_api_route(
    "/situations/{situation_id}", get_situation, methods=["GET"], response_model=SituationWithServices
)
versioned.add_api_route(
    "/voice/transcribe", transcribe, methods=["POST"], response_model=Transcription
)
versioned.add_api_route(
    "/voice/synthesize", synthesize, methods=["POST"], response_class=Response,
    responses={200: {"content": {"audio/ogg": {"schema": {"type": "string", "format": "binary"}}}}},
)

keys = APIRouter(
    prefix="/api/admin/api-keys", tags=["admin"], dependencies=[Depends(require_admin)]
)


class CreateApiKeyRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


@keys.get("")
def list_keys(response: Response, store: ApiKeyStore = Depends(get_key_store)) -> list[ApiKeyInfo]:
    response.headers["Cache-Control"] = "no-store"
    return store.list()


@keys.post("", status_code=201)
def create_key(
    body: CreateApiKeyRequest, response: Response, store: ApiKeyStore = Depends(get_key_store)
) -> CreatedApiKey:
    response.headers["Cache-Control"] = "no-store"
    return store.create(body.name)


@keys.delete("/{key_id}", status_code=204)
def revoke_key(key_id: str, store: ApiKeyStore = Depends(get_key_store)) -> Response:
    if not store.revoke(key_id):
        raise HTTPException(status_code=404, detail="API-ключ не найден")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


integration_router.include_router(versioned)
integration_router.include_router(keys)
