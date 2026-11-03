"""REST API. Контракт синхронизирован с frontend/src/api.ts."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile

from app.models.schemas import ChatRequest, ChatResponse, Situation
from app.services.catalog import Catalog
from app.voice.gateway import VoiceGateway


def get_catalog(request: Request) -> Catalog:
    return request.app.state.catalog


def get_voice(request: Request) -> VoiceGateway:
    return request.app.state.voice


api_router = APIRouter(prefix="/api")


@api_router.get("/services")
def list_services(catalog: Catalog = Depends(get_catalog)) -> list[dict]:
    """Каталог услуг. Черновики не отдаём: карточка ждёт проверки человеком."""
    return [
        s.model_dump() for s in catalog.all() if s.status != "черновик"
    ]


@api_router.get("/services/{service_id}")
def get_service(service_id: str, catalog: Catalog = Depends(get_catalog)) -> dict:
    service = catalog.get(service_id)
    if service is None or service.status == "черновик":
        raise HTTPException(status_code=404, detail="Услуга не найдена")
    return service.model_dump()


@api_router.get("/situations")
def list_situations(catalog: Catalog = Depends(get_catalog)) -> list[Situation]:
    return catalog.situations()


@api_router.get("/situations/{situation_id}")
def get_situation(situation_id: str, catalog: Catalog = Depends(get_catalog)) -> dict:
    situation = next(
        (s for s in catalog.situations() if s.id == situation_id), None
    )
    if situation is None:
        raise HTTPException(status_code=404, detail="Ситуация не найдена")
    services = [catalog.get(sid) for sid in situation.service_ids]
    return {
        **situation.model_dump(),
        "services": [s.model_dump() for s in services if s is not None],
    }


@api_router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    orchestrator = request.app.state.orchestrator
    return orchestrator.answer(body)


@api_router.post("/voice/transcribe")
async def transcribe(
    file: UploadFile,
    voice: VoiceGateway = Depends(get_voice),
) -> dict:
    """Голосовой вход портала/киоска (webm/opus → текст)."""
    if not voice.available():
        raise HTTPException(
            status_code=503,
            detail="Голосовой шлюз недоступен: не установлен ffmpeg",
        )
    suffix = (file.filename or "audio.webm").rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as fh:
        fh.write(await file.read())
        tmp = Path(fh.name)
    try:
        text = voice.transcribe_file(tmp)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return {"text": text}


@api_router.post("/voice/synthesize")
def synthesize(
    body: dict,
    voice: VoiceGateway = Depends(get_voice),
) -> Response:
    """Текст → OGG/Opus (озвучка ответа на портале/киоске).

    Тело: {"text": "...", "lang": "ru"}. Если TTS-окружение не готово — 503:
    фронт в этом случае просто показывает текст.
    """
    text = (body or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Пустой text")
    if len(text) > 2000:
        text = text[:2000]  # ответ длиннее ~1,5 минут речи не озвучиваем
    lang = (body or {}).get("lang") or "ru"
    if not voice.tts_available():
        raise HTTPException(
            status_code=503,
            detail="TTS недоступен: не установлен torch или ffmpeg",
        )
    try:
        audio = voice.synthesize_to_ogg(text, lang=lang)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/ogg")
