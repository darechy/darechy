"""REST API. Контракт синхронизирован с frontend/src/api.ts."""

import tempfile
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.models.schemas import ChatRequest, ChatResponse, Situation
from app.services.catalog import Catalog
from app.voice.gateway import AUDIO_INPUT_FORMATS, VoiceGateway


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
        "services": [s.model_dump() for s in services if s is not None and s.status != "черновик"],
    }


@api_router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    return answer(body, request, namespace="portal")


def answer(body: ChatRequest, request: Request, namespace: str) -> ChatResponse:
    orchestrator = request.app.state.orchestrator
    if body.session_id:
        body = body.model_copy(update={"session_id": f"{namespace}:{body.session_id}"})
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
    suffix = Path(file.filename or "audio.webm").suffix.lower()
    if suffix not in AUDIO_INPUT_FORMATS:
        raise HTTPException(status_code=415, detail="Формат аудио не поддерживается")
    limit = 10 * 1024 * 1024
    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail="Аудиофайл больше 10 МиБ")
    audio = await file.read(limit + 1)
    if len(audio) > limit:
        raise HTTPException(status_code=413, detail="Аудиофайл больше 10 МиБ")
    if not audio:
        raise HTTPException(status_code=422, detail="Пустой аудиофайл")
    try:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory) / f"audio{suffix}"
            await run_in_threadpool(tmp.write_bytes, audio)
            text = await run_in_threadpool(voice.transcribe_file, tmp)
    except (CalledProcessError, TimeoutExpired) as exc:
        raise HTTPException(status_code=422, detail="Не удалось прочитать аудио") from exc
    except (RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=503, detail="Распознавание речи недоступно") from exc
    return {"text": text}


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    lang: str | None = None


@api_router.post("/voice/synthesize")
def synthesize(
    body: SynthesisRequest,
    voice: VoiceGateway = Depends(get_voice),
) -> Response:
    """Текст → OGG/Opus (озвучка ответа на портале/киоске).

    Тело: {"text": "...", "lang": "ru"}. Если TTS-окружение не готово — 503:
    фронт в этом случае просто показывает текст.
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Пустой text")
    if len(text) > 2000:
        text = text[:2000]  # ответ длиннее ~1,5 минут речи не озвучиваем
    lang = body.lang or "ru"
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
