"""Оркестратор диалога — сердце RAG с цитатами.

Принципы (незыблемые, см. docs/план-разработки.md):
1. Ответ строится ТОЛЬКО по извлечённым чанкам корпуса.
2. Каждый ответ сопровождается цитатами с точным местом в акте.
3. Нет релевантного источника → честный отказ-шаблон, никаких выдумок.
4. LLM недоступна → экстрактивный режим: ответ = дословные фрагменты чанков
   с цитатами. Демо не падает, guardrail не нарушается.
"""

import re
import time

from app.chat.router import ServiceRouter
from app.core.config import Settings
from app.llm.client import LlmClient
from app.models.schemas import ChatRequest, ChatResponse, Citation
from app.retrieval.store import ChunkStore

REFUSAL_TEMPLATE = (
    "Честный ответ: в базе знаний «ДаРэчы» по этому вопросу подтверждённой "
    "информации нет. Я отвечаю только по официальным нормативным актам и не "
    "рассказываю то, чего не могу процитировать. Уточните вопрос или "
    "обратитесь в подразделение по труду, занятости и социальной защите."
)

_MARK_RE = re.compile(r"\[(?P<ids>[a-zA-Z0-9_\-,\s]+)\]")
_MAX_QUOTE_CHARS = 600

SYSTEM_PROMPT = (
    "Ты — «ДаРэчы», ассистент по государственным услугам Беларуси. Ты отвечаешь "
    "исключительно по присланным тебе фрагментам нормативных правовых актов. "
    "Правила: (1) опирайся только на фрагменты, не добавляй знания из себя; "
    "(2) на каждый факт ставь ссылку-маркер вида [chunk_id] на соответствующий "
    "фрагмент; (3) если во фрагментах нет ответа на вопрос — выведи ровно слово "
    "ОТКАЗ и ничего больше; (4) пиши кратко и по-человечески, без канцелярита."
)


def _citations_from(hit_ids: list[str], store: ChunkStore) -> list[Citation]:
    citations: list[Citation] = []
    for chunk_id in hit_ids:
        hit = store.get(chunk_id)
        if hit is None:
            continue
        chunk = hit.chunk
        citations.append(
            Citation(
                chunk_id=chunk.chunk_id,
                doc_title=chunk.doc_title or "",
                doc_number=chunk.doc_number,
                doc_date=chunk.doc_date,
                location=chunk.location.label() or None,
                url=chunk.doc_url,
                quote=chunk.text[:_MAX_QUOTE_CHARS],
            )
        )
    return citations


def _extract_markers(answer: str) -> list[str]:
    """Достаёт [chunk_id] маркеры из ответа модели."""
    ids: list[str] = []
    for match in _MARK_RE.finditer(answer):
        for raw_id in match["ids"].split(","):
            chunk_id = raw_id.strip()
            if chunk_id:
                ids.append(chunk_id)
    return ids


def _strip_markers(answer: str) -> str:
    cleaned = _MARK_RE.sub("", answer)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)  # «величиной .» → «величиной.»
    return re.sub(r"[ \t]+", " ", cleaned).strip()


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: ChunkStore,
        llm: LlmClient,
        router: ServiceRouter | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.router = router

    def answer(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()

        # Guardrail №1: нет релевантных чанков → отказ. Порог применяется
        # к сырым скорам плеч внутри store.search (RRF-скор — ранговый).
        hits = self.store.search(
            request.message,
            self.llm,
            top_k=request.top_k,
            score_threshold=self.settings.retrieval_score_threshold,
        )
        suggested = self.router.route(request.message) if self.router else []
        if not hits:
            return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                              suggested=suggested, started=started)

        llm_answer = self.llm.chat(SYSTEM_PROMPT, self._build_context(request, hits))

        if llm_answer is None:
            # Guardrail №2: LLM недоступна — экстрактивный режим, не выдумываем.
            return self._done(
                *self._extractive_answer(hits),
                suggested=suggested,
                started=started,
            )

        if "ОТКАЗ" in llm_answer and len(llm_answer) < 400:
            # Модель честно сказала «ответа в актах нет» — превращаем в отказ.
            return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                              suggested=suggested, started=started)

        marker_ids = [i for i in _extract_markers(llm_answer) if self.store.get(i)]
        if marker_ids:
            citations = _citations_from(marker_ids, self.store)
        else:
            # Модель не промаркировала — цитируем лучший чанк вместо «веры на слово».
            citations = _citations_from([hits[0].chunk.chunk_id], self.store)
        return self._done(
            _strip_markers(llm_answer), citations, refusal=False,
            suggested=suggested, started=started,
        )

    # --- внутреннее ---------------------------------------------------------

    def _build_context(self, request: ChatRequest, hits) -> str:
        blocks: list[str] = []
        for hit in hits:
            chunk = hit.chunk
            blocks.append(
                f"[{chunk.chunk_id}] {chunk.doc_title}"
                f" ({chunk.doc_number}, {chunk.doc_date})\n{chunk.text}"
            )
        context = "\n\n".join(blocks)
        if request.service_id:
            context = f"Контекст страницы услуги: {request.service_id}\n\n{context}"
        return context

    def _extractive_answer(self, hits) -> tuple[str, list[Citation]]:
        """Fallback без LLM: дословный фрагмент лучшего чанка + цитата."""
        best = hits[0]
        snippet = best.chunk.text[:_MAX_QUOTE_CHARS]
        answer = (
            "Модель генерации сейчас недоступна, поэтому дословно из нормативного "
            f"акта ({best.chunk.doc_title}):\n\n{snippet}"
        )
        citations = _citations_from([best.chunk.chunk_id], self.store)
        return answer, citations

    def _done(self, answer, citations, refusal=False, suggested=None, started=0.0) -> ChatResponse:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ChatResponse(
            answer=answer,
            citations=citations,
            refusal=refusal,
            suggested_services=suggested or [],
            latency_ms=latency_ms,
        )
