"""Админские роуты: всё, что раньше требовало правки кода.

Три задачи, ради которых это существует:
1. **Добавить акт в базу знаний** — загрузить выгрузку с pravo.by, разобрать
   на пункты с проверкой целостности, посмотреть глазами, закоммитить в индекс.
2. **Добавить услугу** — каталог портала редактируется как данные, с чек-листом
   и источником, выбранным из корпуса (а не набранным руками).
3. **Доучить модель** — эталонный ответ с триггерами и обязательной ссылкой
   на норму. Отвечает мгновенно и не зависит от настроения модели.

Защита: заголовок `X-Admin-Token` для запросов к данным.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from secrets import compare_digest

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse

from app.core.config import BACKEND_DIR, Settings, get_settings
from app.kb.corpus import (
    build_curated_doc,
    corpus_inventory,
    parse_act_markdown,
)
from app.kb.positions import extract_positions
from app.kb.procedures import parse_sections
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    Service,
    TaughtAnswer,
    TaughtAnswerCreate,
    TaughtAnswerUpdate,
)
from app.services.admin_store import ServiceCatalogStore, chunk_options, slug
from app.services.drafter import PERECHEN_PREFIX, draft_from_procedure

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])





# --- доступ -----------------------------------------------------------------


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Settings:
    """Проверяет токен для доступа к административным данным."""
    settings = get_settings()
    tokens = [settings.admin_token, *settings.admin_accounts.values()]
    if not any(tokens):
        raise HTTPException(503, detail={"reason": "Админка не настроена"})
    if not x_admin_token or not any(
        token and compare_digest(x_admin_token.encode(), token.encode()) for token in tokens
    ):
        raise HTTPException(401, detail={"reason": "Неверный токен админки"})
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


@admin_router.get("/metrics")
def metrics(hours: int = Query(default=24, ge=1, le=720), _: Settings = Depends(require_admin)) -> dict:
    from app.services.telemetry import snapshot

    return snapshot(hours)


@admin_router.get("", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    """Одностраничная админка. Один файл, без сборки — правится как текст."""
    page = BACKEND_DIR / "app" / "admin" / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail={"reason": "admin/index.html не найден"})
    return HTMLResponse(
        page.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@admin_router.get("/logo.svg", include_in_schema=False)
def admin_logo() -> FileResponse:
    return FileResponse(BACKEND_DIR.parent / "frontend" / "public" / "logo.svg", media_type="image/svg+xml")


@admin_router.post("/diagnostics/chat", response_model=ChatResponse)
def diagnostic_chat(
    body: ChatRequest, request: Request, _: Settings = Depends(require_admin)
) -> ChatResponse:
    """Проверяет ответ без сохранения реплики в истории диалога."""
    return request.app.state.orchestrator.answer(body.model_copy(update={"session_id": None}))


# --- корпус: добавить акт ---------------------------------------------------


@admin_router.get("/corpus")
def corpus_list(
    request: Request, _: Settings = Depends(require_admin)
) -> dict:
    """Что уже в базе знаний: акты, чанки, статусы."""
    settings = get_settings()
    store = getattr(request.app.state, "store", None)
    return {
        "docs": corpus_inventory(settings.corpus_dir, settings.raw_dir),
        "indexed_chunks": store.size() if store else 0,
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
    prefix: str | None = Query(default=None, pattern=r"^[\w-]{1,100}$"),
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

    reindexed = await run_in_threadpool(_reindex, request)
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
    file: str = Query(pattern=r"^[\w-]{1,150}\.json$"),
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
    if path.resolve().parent != settings.corpus_dir.resolve():
        raise HTTPException(status_code=422, detail={"reason": "Недопустимый путь к акту"})
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


@admin_router.post("/corpus/reindex")
def corpus_reindex(request: Request, _: Settings = Depends(require_admin)) -> dict:
    return _reindex(request)


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


@admin_router.get("/situations")
def situations_admin_list(request: Request, _: Settings = Depends(require_admin)) -> list[dict]:
    """Ситуации так, как их увидит портал, плюс черновики в стороне."""
    catalog = getattr(request.app.state, "catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail={"reason": "каталог не инициализирован"})
    situations = {
        situation.id: {
            "id": situation.id,
            "title": situation.title,
            "service_ids": situation.service_ids,
        }
        for situation in catalog.situations()
    }
    for sid, config in _catalog_store(get_settings()).situation_titles().items():
        if sid not in situations:
            situations[sid] = {
                "id": sid,
                "title": config.get("title", sid) if isinstance(config, dict) else str(config),
                "service_ids": config.get("service_ids", []) if isinstance(config, dict) else [],
            }
    return list(situations.values())


@admin_router.post("/situations/{situation_id}")
def situation_save(
    situation_id: str,
    _: Settings = Depends(require_admin),
    title: str | None = Query(default=None),
    note: str | None = Query(default=None),
    service_ids: list[str] | None = Query(default=None, description="состав услуг (сквозная ситуация)"),
) -> dict:
    """Правит ситуацию: название и состав услуг. Сквозная ситуация — это явный список."""
    entry = _catalog_store(get_settings()).upsert_situation(
        situation_id, title=title, service_ids=service_ids, note=note
    )
    return {"saved": entry}


@admin_router.get("/chunks")
def chunks_for_picker(
    request: Request,
    q: str = Query(default=""),
    _: Settings = Depends(require_admin),
) -> list[dict]:
    """Чанки корпуса для выбора источника услуги — по поиску, а не по памяти."""
    return chunk_options(_get_chunks(request), query=q)


# --- каталог из перечня административных процедур ---------------------------
#
# Смысл: новая категория услуг = новый РАЗДЕЛ перечня, а не новый код.
# Импорт делает три вещи сразу: разбирает позиции раздела, кладёт их в корпус
# (иначе у карточек не будет настоящих цитат) и собирает черновики карточек.
# На портал черновики не попадают: их утверждает человек.


@admin_router.get("/sections")
def sections_list(_: Settings = Depends(require_admin)) -> list[dict]:
    """Разделы перечня с числом процедур — что можно импортировать."""
    settings = get_settings()
    path = _perechen_path(settings)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"reason": "файл перечня не найден в kb_corpus/raw"})
    raw = path.read_text(encoding="utf-8")
    return [
        {"name": name, "procedures": len(procedures)}
        for name, procedures in parse_sections(raw)
    ]


@admin_router.get("/services/drafts")
def drafts_list(request: Request, _: Settings = Depends(require_admin)) -> dict:
    """Черновики карточек и сводка по ним."""
    store = _drafts(request)
    return {
        "stats": store.stats(),
        "items": [draft.model_dump(mode="json") for draft in store.all()],
    }


@admin_router.post("/services/import")
def services_import(
    request: Request,
    section: str | None = Query(default=None, description="раздел перечня; пусто — все"),
    limit: int = Query(default=0, ge=0, description="0 — без ограничения"),
    polish: bool = Query(default=False, description="переписать заголовки через ИИ"),
    _: Settings = Depends(require_admin),
) -> dict:
    """Импортирует раздел перечня: позиции в корпус + черновики карточек."""
    settings = get_settings()
    path = _perechen_path(settings)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"reason": "файл перечня не найден в kb_corpus/raw"})

    raw = path.read_text(encoding="utf-8")
    sections = parse_sections(raw)
    if section:
        wanted = section.strip().upper()
        sections = [(name, procs) for name, procs in sections if name.upper() == wanted]
        if not sections:
            raise HTTPException(status_code=404, detail={"reason": f"раздел «{section}» не найден"})

    procedures = [proc for _name, procs in sections for proc in procs]
    if limit:
        procedures = procedures[:limit]
    if not procedures:
        raise HTTPException(status_code=422, detail={"reason": "в выбранных разделах нет процедур"})

    # 1. Позиции — в корпус, чтобы у карточек были настоящие цитаты.
    numbers = tuple(proc.number for proc in procedures)
    chunks, missing = extract_positions(
        raw,
        numbers,
        doc_prefix=PERECHEN_PREFIX,
        doc_title="Перечень административных процедур (Указ от 26.04.2010 № 200)",
    )
    corpus_note = "позиции не добавлялись"
    if chunks:
        known = {chunk.chunk_id for chunk in _get_chunks(request)}
        fresh = [chunk for chunk in chunks if chunk.chunk_id not in known]
        if fresh:
            settings.corpus_dir.mkdir(parents=True, exist_ok=True)
            target = settings.corpus_dir / f"{PERECHEN_PREFIX}-razdel-{slug(section or 'vse')[:30]}.json"
            doc_meta, _title, _ = parse_act_markdown(raw)
            _merge_positions_into_file(target, doc_meta, chunks)
            corpus_note = f"позиций в корпусе: +{len(fresh)} ({target.name})"

    # 2. Черновики карточек.
    llm = getattr(request.app.state, "llm", None)
    drafts = [
        draft_from_procedure(
            proc,
            llm=llm,
            polish=polish,
            position_chunk_id=f"{PERECHEN_PREFIX}_поз-{proc.number.replace('.', '-')}",
        )
        for proc in procedures
    ]
    result = _drafts(request).merge(drafts)

    reindexed = _reindex(request) if chunks else {}
    logger.info("Импорт раздела %s: %d процедур, черновиков %s", section or "все", len(procedures), result)
    return {
        "section": section or "все разделы",
        "procedures": len(procedures),
        "corpus": corpus_note,
        "missing_positions": missing,
        "drafts": result,
        "reindex": reindexed,
    }


@admin_router.post("/services/drafts/{draft_id}/approve")
def draft_approve(
    draft_id: str, request: Request, _: Settings = Depends(require_admin)
) -> dict:
    """Утверждает черновик: переносит в рабочий каталог со статусом «проверена»."""
    settings = get_settings()
    store = _drafts(request)
    draft = store.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"reason": f"черновик {draft_id} не найден"})

    approved = draft.model_copy(update={"status": "проверена"})
    _catalog_store(settings).upsert(approved)
    store.delete(draft_id)

    # Каталог читается по mtime — портал подхватит карточку сразу.
    return {"approved": approved.model_dump(mode="json")}


@admin_router.delete("/services/drafts/{draft_id}")
def draft_reject(draft_id: str, request: Request, _: Settings = Depends(require_admin)) -> dict:
    if not _drafts(request).delete(draft_id):
        raise HTTPException(status_code=404, detail={"reason": f"черновик {draft_id} не найден"})
    return {"rejected": draft_id}


def _perechen_path(settings: Settings) -> Path:
    """Файл перечня в raw/: ищем по префиксу, а не по точному имени."""
    matches = sorted(settings.raw_dir.glob("*ukaz*perechen*.md"))
    return matches[0] if matches else settings.raw_dir / "2010_200_ukaz-perechen-administrativnykh-procedur.md"


def _drafts(request: Request):
    store = getattr(request.app.state, "drafts", None)
    if store is None:
        raise HTTPException(status_code=503, detail={"reason": "хранилище черновиков не инициализировано"})
    return store


def _merge_positions_into_file(target: Path, doc_meta: dict, chunks: list) -> None:
    """Дописывает позиции в файл раздела, не теряя уже лежащие там."""
    existing: list = []
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8")).get("chunks", [])
        except json.JSONDecodeError:
            existing = []
    known = {chunk.get("chunk_id") for chunk in existing}
    merged = [*existing, *(c.model_dump(mode="json") for c in chunks if c.chunk_id not in known)]
    target.write_text(
        json.dumps({"doc": doc_meta, "chunks": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


@admin_router.get("/knowledge")
def knowledge_graph(request: Request, _: Settings = Depends(require_admin)) -> dict:
    """Реальные связи каталога с пунктами корпуса."""
    documents = {}
    for chunk in _get_chunks(request):
        key = (chunk.doc_number, chunk.doc_date, chunk.doc_title)
        document = documents.setdefault(key, {"title": chunk.doc_title, "number": chunk.doc_number, "points": []})
        document["points"].append({
            "id": chunk.chunk_id, "label": chunk.location.label(), "status": chunk.status,
            "text": chunk.text,
        })
    return {
        "documents": list(documents.values()),
        "services": [
            {"title": service.title, "points": [source.point for source in service.sources if source.point]}
            for service in request.app.state.catalog.all() if service.status != "черновик"
        ],
    }


@admin_router.get("/models")
def admin_models(request: Request, _: Settings = Depends(require_admin)) -> dict:
    llm = request.app.state.llm
    return {"active": llm.chat_model, "available": llm.available_models(),
            "embedding": llm.embedding_model, "dense_ready": request.app.state.store.dense_available}


@admin_router.post("/models")
def select_model(request: Request, model: str = Query(min_length=1),
                 _: Settings = Depends(require_admin)) -> dict:
    llm = request.app.state.llm
    if model not in llm.available_models() or model.split(":")[0] == llm.embedding_model.split(":")[0]:
        raise HTTPException(400, detail="Выберите установленную модель генерации")
    llm.chat_model = model
    return {"active": llm.chat_model}
