"""Админские роуты: всё, что раньше требовало правки кода.

Три задачи, ради которых это существует:
1. **Добавить акт в базу знаний** — загрузить выгрузку с pravo.by, разобрать
   на пункты с проверкой целостности, посмотреть глазами, закоммитить в индекс.
2. **Добавить услугу** — каталог портала редактируется как данные, с чек-листом
   и источником, выбранным из корпуса (а не набранным руками).
3. **Доучить модель** — эталонный ответ с триггерами и обязательной ссылкой
   на норму. Отвечает мгновенно и не зависит от настроения модели.

Защита: заголовок `X-Admin-Token` (или ?token= для открытия UI в браузере).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.core.config import BACKEND_DIR, Settings, get_settings
from app.kb.corpus import build_curated_doc, corpus_inventory
from app.models.schemas import (
    Service,
    TaughtAnswer,
    TaughtAnswerCreate,
    TaughtAnswerUpdate,
)
from app.services.admin_store import ServiceCatalogStore, chunk_options, slug

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# --- доступ -----------------------------------------------------------------


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
) -> Settings:
    """Проверяет токен админки. `?token=` — чтобы открыть UI прямо в браузере."""
    settings = get_settings()
    provided = x_admin_token or token
    if not settings.admin_token:
        raise HTTPException(
            status_code=503,
            detail={"reason": "ADMIN_TOKEN не задан в backend/.env — админка выключена"},
        )
    if provided != settings.admin_token:
        raise HTTPException(
            status_code=401,
            detail={"reason": "неверный токен админки (заголовок X-Admin-Token)"},
        )
    return settings


def _get_taught(request: Request):
    store = getattr(request.app.state, "taught", None)
    if store is None:
        raise HTTPException(status_code=503, detail={"reason": "слой выученных ответов не инициализирован"})
    return store


def _get_chunks(request: Request) -> list:
    """Чанки корпуса в том виде, в котором их видит админка."""
    return getattr(request.app.state, "corpus_chunks", [])


def _catalog_store(settings: Settings) -> ServiceCatalogStore:
    return ServiceCatalogStore(settings.services_file)


# --- UI ---------------------------------------------------------------------


@admin_router.get("", response_class=HTMLResponse)
def admin_page(_: Settings = Depends(require_admin)) -> HTMLResponse:
    """Одностраничная админка. Один файл, без сборки — правится как текст."""
    page = BACKEND_DIR / "app" / "admin" / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail={"reason": "admin/index.html не найден"})
    return HTMLResponse(page.read_text(encoding="utf-8"))


# --- корпус: добавить акт ---------------------------------------------------


@admin_router.get("/corpus")
def corpus_list(
    request: Request, _: Settings = Depends(require_admin)
) -> dict:
    """Что уже в базе знаний: акты, чанки, статусы."""
    settings = get_settings()
    return {
        "docs": corpus_inventory(settings.corpus_dir, settings.raw_dir),
        "indexed_chunks": len(_get_chunks(request)),
        "indexed_statuses": sorted(settings.allowed_statuses),
    }


@admin_router.post("/corpus/preview")
async def corpus_preview(
    request: Request,
    file: UploadFile | None = File(default=None),
    _: Settings = Depends(require_admin),
) -> dict:
    """Разбирает выгрузку акта и показывает результат, ничего не записывая.

    Показать до сохранения — принципиально: админ должен увидеть, что разбор
    не потерял текст, прежде чем это попадёт в индекс.
    """
    raw = await _read_upload(file, request)
    prefix = Path(_filename(file)).stem if file is not None else "uploaded"

    doc, errors = build_curated_doc(raw, doc_prefix=prefix)
    if doc is None:
        raise HTTPException(status_code=422, detail={"reason": "; ".join(errors)})

    sizes = [len(c.text) for c in doc.chunks]
    return {
        "doc": doc.doc.model_dump(mode="json"),
        "chunks": len(doc.chunks),
        "chars": sum(sizes),
        "size_min": min(sizes),
        "size_max": max(sizes),
        "suggested_prefix": slug(doc.doc.title)[:40],
        "statuses": sorted({c.status for c in doc.chunks}),
        "sample": [
            {
                "chunk_id": c.chunk_id,
                "title": c.title,
                "text": c.text[:300],
                "chars": len(c.text),
            }
            for c in doc.chunks[:8]
        ],
        "integrity": "склейка пунктов совпала с исходным текстом",
    }


@admin_router.post("/corpus/import")
async def corpus_import(
    request: Request,
    file: UploadFile | None = File(default=None),
    prefix: str | None = Query(default=None),
    status: str = Query(default="восстановлен"),
    overwrite: bool = Query(default=False),
    _: Settings = Depends(require_admin),
) -> dict:
    """Сохраняет разобранный акт в `kb_corpus/curated/` и переиндексирует корпус."""
    settings = get_settings()
    raw = await _read_upload(file, request)
    doc, errors = build_curated_doc(raw, doc_prefix=prefix or "act", status=status)
    if doc is None:
        raise HTTPException(status_code=422, detail={"reason": "; ".join(errors)})

    settings.corpus_dir.mkdir(parents=True, exist_ok=True)
    target = settings.corpus_dir / f"{prefix or 'act'}.json"
    if target.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail={"reason": f"{target.name} уже есть — включите overwrite, если заменяем"},
        )
    target.write_text(
        json.dumps(
            {"doc": doc.doc.model_dump(mode="json"), "chunks": [c.model_dump(mode="json") for c in doc.chunks]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Сохраняем оригинал рядом: конвейер требует хранить выгрузку как есть.
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    (settings.raw_dir / f"{prefix or 'act'}.md").write_text(raw, encoding="utf-8")

    reindexed = _reindex(request)
    logger.info("Импортирован акт %s: %d чанков", target.name, len(doc.chunks))
    return {
        "saved": target.name,
        "raw_saved": f"{prefix or 'act'}.md",
        "chunks": len(doc.chunks),
        "reindex": reindexed,
    }


@admin_router.post("/corpus/status")
def corpus_set_status(
    request: Request,
    file: str = Query(),
    chunk_id: str | None = Query(default=None),
    status: str = Query(),
    _: Settings = Depends(require_admin),
) -> dict:
    """Меняет статус чанков: «восстановлен» → «проверен» после сверки глазами.

    Правило конвейера: статус «проверен» ставит второй человек, сверив текст
    с оригиналом. Админка даёт это сделать, не открывая редактор.
    """
    settings = get_settings()
    path = settings.corpus_dir / file
    if not path.exists():
        raise HTTPException(status_code=404, detail={"reason": f"{file} не найден"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for chunk in payload.get("chunks", []):
        if chunk_id and chunk.get("chunk_id") != chunk_id:
            continue
        chunk["status"] = status
        changed += 1
    if changed == 0:
        raise HTTPException(status_code=404, detail={"reason": "чанк не найден"})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"file": file, "changed": changed, "status": status, "reindex": _reindex(request)}


def _reindex(request: Request) -> dict:
    """Перечитывает корпус и пересобирает индекс (BM25 + Qdrant)."""
    settings = get_settings()
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "индекс недоступен"}
    from app.kb.ingest import collect_chunks, load_curated

    docs, errors = load_curated(settings.corpus_dir)
    chunks = collect_chunks(docs)
    indexed = store.rebuild(chunks)
    request.app.state.corpus_chunks = chunks
    taught = getattr(request.app.state, "taught", None)
    if taught is not None:
        taught.set_corpus(chunks)
    uploaded = store.dense_index(getattr(request.app.state, "llm", None)) if hasattr(
        request.app.state, "llm"
    ) else 0
    return {"chunks": indexed, "vectors": uploaded, "errors": errors}


async def _read_upload(file: UploadFile | None, request: Request) -> str:
    """Текст акта: либо из загруженного файла, либо из тела запроса."""
    if file is not None:
        return (await file.read()).decode("utf-8", errors="replace")
    body = await request.body()
    if not body:
        raise HTTPException(
            status_code=400,
            detail={"reason": "передайте файл (multipart) или текст акта телом запроса"},
        )
    return body.decode("utf-8", errors="replace")


def _filename(file: UploadFile | None) -> str:
    return getattr(file, "filename", None) or "uploaded"


# --- услуги -----------------------------------------------------------------


@admin_router.get("/services")
def services_list(request: Request, _: Settings = Depends(require_admin)) -> list[dict]:
    settings = get_settings()
    store = _catalog_store(settings)
    titles = store.situation_titles()
    return [
        {
            **service.model_dump(mode="json"),
            "situation_title": titles.get(service.situation, service.situation),
        }
        for service in store.all()
    ]


@admin_router.post("/services")
def service_save(
    service: Service, _: Settings = Depends(require_admin)
) -> dict:
    settings = get_settings()
    store = _catalog_store(settings)
    if not service.id:
        service = service.model_copy(update={"id": slug(service.title)})
    saved = store.upsert(service)
    return {"saved": saved.model_dump(mode="json")}


@admin_router.post("/services/new")
def service_new(
    title: str = Query(min_length=3),
    situation: str = Query(default="rozhdenie-rebenka"),
    situation_title: str | None = Query(default=None),
    _: Settings = Depends(require_admin),
) -> dict:
    """Создаёт заготовку услуги: дальше она заполняется в UI."""
    settings = get_settings()
    store = _catalog_store(settings)
    service = store.create_from_title(title, situation)
    if situation_title:
        store.set_situation_title(situation, situation_title)
    return {"created": service.model_dump(mode="json")}


@admin_router.delete("/services/{service_id}")
def service_delete(service_id: str, _: Settings = Depends(require_admin)) -> dict:
    settings = get_settings()
    deleted = _catalog_store(settings).delete(service_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={"reason": f"услуга {service_id} не найдена"})
    return {"deleted": service_id}


@admin_router.get("/chunks")
def chunks_for_picker(
    request: Request,
    q: str = Query(default=""),
    _: Settings = Depends(require_admin),
) -> list[dict]:
    """Чанки корпуса для выбора источника услуги — по поиску, а не по памяти."""
    return chunk_options(_get_chunks(request), query=q)


# --- выученные ответы (доучивание) -----------------------------------------


@admin_router.get("/taught")
def taught_list(request: Request, _: Settings = Depends(require_admin)) -> dict:
    store = _get_taught(request)
    return {"items": [i.model_dump(mode="json") for i in store.all()], "broken_sources": store.unknown_sources()}


@admin_router.post("/taught")
def taught_create(
    payload: TaughtAnswerCreate, request: Request, _: Settings = Depends(require_admin)
) -> dict:
    store = _get_taught(request)
    unknown = store.sources_ok(payload.sources)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "источник не найден в корпусе: "
                + ", ".join(unknown)
                + ". Выучить ответ без подтверждённой нормы нельзя."
            },
        )
    if not payload.service_id and store.keywords_are_generic(payload.keywords):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "триггеры общие («какие документы», «где получить») — "
                "такой ответ перехватит чужие вопросы. Привяжите ответ к услуге "
                "или дайте специфичные слова."
            },
        )
    created: TaughtAnswer = store.create(payload)
    return {"created": created.model_dump(mode="json")}


@admin_router.put("/taught/{answer_id}")
def taught_update(
    answer_id: str,
    payload: TaughtAnswerUpdate,
    request: Request,
    _: Settings = Depends(require_admin),
) -> dict:
    store = _get_taught(request)
    if payload.sources is not None:
        unknown = store.sources_ok(payload.sources)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"reason": "источник не найден в корпусе: " + ", ".join(unknown)},
            )
    target = store.get(answer_id)
    effective_keywords = payload.keywords if payload.keywords is not None else (
        target.keywords if target else []
    )
    effective_service = payload.service_id if payload.service_id is not None else (
        target.service_id if target else None
    )
    if not effective_service and store.keywords_are_generic(effective_keywords):
        raise HTTPException(
            status_code=422,
            detail={"reason": "триггеры общие и нет привязки к услуге — ответ перехватит чужие вопросы"},
        )
    updated = store.update(answer_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail={"reason": f"ответ {answer_id} не найден"})
    return {"updated": updated.model_dump(mode="json")}


@admin_router.delete("/taught/{answer_id}")
def taught_delete(answer_id: str, request: Request, _: Settings = Depends(require_admin)) -> dict:
    store = _get_taught(request)
    if not store.delete(answer_id):
        raise HTTPException(status_code=404, detail={"reason": f"ответ {answer_id} не найден"})
    return {"deleted": answer_id}
