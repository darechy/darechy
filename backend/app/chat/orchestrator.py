"""Оркестратор диалога — сердце RAG с цитатами.

Принципы (незыблемые, см. docs/план-разработки.md):
1. Ответ строится ТОЛЬКО по извлечённым чанкам корпуса.
2. Каждый ответ сопровождается цитатами с точным местом в акте.
3. Нет релевантного источника → честный отказ-шаблон, никаких выдумок.
4. LLM недоступна → экстрактивный режим: ответ = дословные фрагменты чанков
   с цитатами. Демо не падает, guardrail не нарушается.
"""

import logging
import re
import time
from typing import TYPE_CHECKING

from app.chat.router import ServiceRouter
from app.chat.sessions import SessionMemory
from app.core.config import Settings
from app.llm.client import LlmClient
from app.models.schemas import ChatRequest, ChatResponse, Citation
from app.retrieval.store import ChunkStore, tokenize

if TYPE_CHECKING:  # импорт только для аннотаций: taught.py сам импортирует схемы
    from app.chat.taught import TaughtStore

logger = logging.getLogger(__name__)

REFUSAL_TEMPLATE = (
    "Честный ответ: в базе знаний «ДаРэчы» по этому вопросу подтверждённой "
    "информации нет. Я отвечаю только по официальным нормативным актам и не "
    "рассказываю то, чего не могу процитировать. Уточните вопрос или "
    "обратитесь в подразделение по труду, занятости и социальной защите."
)

# Маркеры вида [7-Z_st-12-п-1]. Кириллица обязательна: id пунктов собираются
# из «пункт N» → «п-1», и без \u0400-\u04FF маркеры молча терялись — модель
# ставила ссылку, а цитата подставлялась «лучшим чанком», то есть не та.
_MARK_RE = re.compile(r"\[(?P<ids>[a-zA-Z0-9_\-\u0400-\u04FF,\s]+)\]")
_MAX_QUOTE_CHARS = 600
# Бюджет фрагмента, который уходит модели. 1800 символов ≈ 600 токенов на статью:
# шесть статей ≈ 3 600 токенов вместо ~9 400 — это и есть лечение галлюцинаций.
_MAX_EXCERPT_CHARS = 1200
_MIN_EXCERPT_CHARS = 400
# Режем по границе предложения, не ломая «п. 2.1.» и «ст. 11».
_SENTENCE_RE = re.compile(r"(?<=[.;])\s+(?=[А-ЯA-Z0-9(])")

SYSTEM_PROMPT = (
    "Ты — «ДаРэчы», ассистент по государственным услугам Беларуси. Ты отвечаешь "
    "исключительно по присланным фрагментам нормативных правовых актов.\n"
    "Правила, которые нельзя нарушать:\n"
    "(1) используй только присланные фрагменты, ничего не добавляй от себя;\n"
    "(2) НИКОГДА не называй номер статьи или пункта, которого нет во фрагментах, "
    "и не пересказывай норму, которой во фрагментах нет;\n"
    "(3) на каждый факт в ответе ставь маркер вида [chunk_id] того фрагмента, "
    "откуда взят факт;\n"
    "(4) если во фрагментах нет прямого ответа на вопрос — ответь ровно одним "
    "словом ОТКАЗ и больше ничем. Не отвечай на смежную тему, не перечисляй "
    "другие пособия, не заполняй пробел догадкой: «похоже по теме» — это не ответ;\n"
    "(5) пиши кратко и по-человечески, без канцелярита."
)

# Модель может «отказаться» своими словами, не написав ровно ОТКАЗ. Раньше
# такой ответ уходил пользователю как обычный, хотя это был отказ.
_REFUSAL_PHRASES = (
    "отказ",
    "нет ответа",
    "не содержится",
    "не содержит",
    "не указано",
    "не указаны",
    "отсутствует",
    "не установлено",
    "не установлен",
    "фрагментах нет",
    "фрагменты не",
    "нет информации",
    "информации нет",
    "не могу ответить",
    "недостаточно",
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


def _looks_like_refusal(answer: str) -> bool:
    """Отказ своими словами, а не только словом ОТКАЗ."""
    lowered = answer.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


_ARTICLE_MENTION_RE = re.compile(r"стать[ияею][\s№]*(\d+(?:-\d+)?)", re.IGNORECASE)


def _mentions_unknown_article(answer: str, hits) -> str | None:
    """Ищет статью, названную моделью, но отсутствующую во фрагментах.

    Реальный случай: на вопрос про пособие при рождении модель написала
    «в соответствии со статьёй 19» — такой нормы во фрагментах не было,
    и ответ оказался выдумкой. Возвращает номер статьи или None.
    """
    allowed: set[str] = set()
    for hit in hits:
        article = hit.chunk.location.article or ""
        match = re.search(r"(\d+(?:-\d+)?)", article)
        if match:
            allowed.add(match.group(1))
    if not allowed:
        return None

    for match in _ARTICLE_MENTION_RE.finditer(answer):
        if match.group(1) not in allowed:
            return match.group(1)
    return None


def _strip_refusal(answer: str) -> str:
    """Убирает слово ОТКАЗ и возвращает содержательную часть ответа.

    Пустая строка означает «модель действительно отказалась» — вызывающий код
    превращает это в отказ-шаблон. Непустая — модель ответила, но зачем-то
    дописала маркер: оставляем текст, маркер убираем.
    """
    content = answer.replace("ОТКАЗ", " ")
    content = _MARK_RE.sub("", content)
    content = re.sub(r"\s+", " ", content).strip(" \t\n-—:,;.")
    return content


def best_excerpt(
    text: str,
    query: str,
    max_chars: int = _MAX_EXCERPT_CHARS,
    min_chars: int = _MIN_EXCERPT_CHARS,
) -> str:
    """Выборка релевантных предложений из статьи — вместо всей статьи целиком.

    Полная статья в промпте топит слабую модель и провоцирует выдуманные нормы.
    Режем по предложениям, оцениваем пересечение с запросом (те же токены и
    стоп-слова, что в BM25), берём лучшие. Если пересечений нет — возвращаем
    начало статьи: цитировать всё равно нечего, но контекст нужен.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text

    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    query_tokens = set(tokenize(query))
    if not sentences or not query_tokens:
        return text[:max_chars]

    scored: list[tuple[float, int]] = []
    for index, sentence in enumerate(sentences):
        tokens = set(tokenize(sentence))
        if not tokens:
            scored.append((0.0, index))
            continue
        # Нормируем на длину: иначе длинные пункты выигрывают у точных коротких.
        overlap = len(tokens & query_tokens) / (len(tokens) ** 0.5)
        scored.append((overlap, index))

    # Идём по убыванию релевантности, добавляем предложения, пока влезаем.
    picked: list[int] = []
    used = 0
    for _, index in sorted(scored, key=lambda item: item[0], reverse=True):
        length = len(sentences[index]) + 1
        if used + length > max_chars and used >= min_chars:
            break
        picked.append(index)
        used += length
        if used >= max_chars:
            break

    if not picked:
        return text[:max_chars]
    # Возвращаем в исходном порядке — так текст статьи читается связно.
    return " ".join(sentences[index] for index in sorted(picked))


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
        sessions: SessionMemory | None = None,
        taught: "TaughtStore | None" = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        # Слой «доучивания»: подтверждённые людьми ответы поверх корпуса.
        self.taught = taught
        self.router = router
        self.sessions = sessions if sessions is not None else SessionMemory()

    def answer(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        history = self.sessions.history(request.session_id)
        suggested = self.router.route(request.message) if self.router else []

        # Слой доучивания: ответ, подтверждённый администратором, важнее
        # генерации. Отвечает мгновенно и не зависит от качества модели, но
        # цитаты всё равно берутся из корпуса — не из текста администратора.
        taught = self._taught_answer(request)
        if taught is not None:
            self._remember(request, taught.answer)
            return self._done(
                taught.answer,
                taught.citations,
                suggested=suggested,
                started=started,
                source="taught",
            )

        # Уточняющие вопросы («а какие документы?») теряют смысл без контекста:
        # в поисковый запрос подмешиваем предыдущую реплику пользователя.
        query = request.message
        if history:
            last_user = next(
                (turn["content"] for turn in reversed(history) if turn["role"] == "user"),
                "",
            )
            if last_user and request.message.lower().startswith(
                ("а ", "и ", "а какие", "а что", "а где", "а когда", "а сколько")
            ):
                query = f"{last_user} {request.message}"

        # Guardrail №1: нет релевантных чанков → отказ. Порог применяется
        # к сырым скорам плеч внутри store.search (RRF-скор — ранговый).
        hits = self.store.search(
            query,
            self.llm,
            top_k=request.top_k,
            score_threshold=self.settings.retrieval_score_threshold,
        )
        if not hits:
            self._remember(request, None)
            return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                              suggested=suggested, started=started)

        llm_answer = self.llm.chat(
            SYSTEM_PROMPT, self._build_context(request, hits, history)
        )

        if llm_answer is None:
            # Guardrail №2: LLM недоступна — экстрактивный режим, не выдумываем.
            answer, citations = self._extractive_answer(hits)
            self._remember(request, answer)
            return self._done(
                answer, citations,
                suggested=suggested,
                started=started,
            )

        if _looks_like_refusal(llm_answer):
            # Модель умеет дописать ОТКАЗ в конец нормального ответа, забыв, что
            # её просили вывести ровно это слово. Раньше маркер просачивался
            # в ответ пользователю («... ОТКАЗ» после верного абзаца).
            # Смотрим на содержательную часть: пусто — честный отказ,
            # есть текст — это ответ, маркер просто выкидываем.
            content = _strip_refusal(llm_answer)
            if not content:
                self._remember(request, None)
                return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                                  suggested=suggested, started=started)
            llm_answer = content

        # Guardrail No3: модель назвала статью, которой нет во фрагментах.
        # Это признак выдуманной нормы — такой ответ отдавать нельзя.
        invented = _mentions_unknown_article(llm_answer, hits)
        if invented is not None:
            logger.warning(
                "Модель сослалась на статью %s, которой нет во фрагментах — "
                "отдаём дословный фрагмент вместо ответа модели",
                invented,
            )
            answer, citations = self._extractive_answer(hits)
            self._remember(request, answer)
            return self._done(
                answer, citations,
                suggested=suggested,
                started=started,
                source="extractive",
            )

        marker_ids = [i for i in _extract_markers(llm_answer) if self.store.get(i)]
        if marker_ids:
            citations = _citations_from(marker_ids, self.store)
        else:
            # Модель не промаркировала — цитируем лучший чанк вместо «веры на слово».
            citations = _citations_from([hits[0].chunk.chunk_id], self.store)
        answer = _strip_markers(llm_answer)
        self._remember(request, answer)
        return self._done(
            answer, citations, refusal=False,
            suggested=suggested, started=started,
        )

    # --- внутреннее ---------------------------------------------------------

    def _taught_answer(self, request: ChatRequest) -> ChatResponse | None:
        """Проверяет слой доучивания. None — выученного ответа нет, идём искать."""
        if self.taught is None:
            return None
        match = self.taught.match(request.message, service_id=request.service_id)
        if match is None:
            return None

        chunks_by_id = {
            chunk_id: hit.chunk
            for chunk_id in (source.chunk_id for source in match.taught.sources)
            if (hit := self.store.get(chunk_id)) is not None
        }
        citations = self.taught.citations_for(match.taught, chunks_by_id)
        if not citations:
            # Источники выученного ответа исчезли из корпуса: отдавать ответ
            # без действующей нормы нельзя — пусть работает обычный путь.
            logger.warning(
                "Выученный ответ %s пропущен: источники не найдены в корпусе", match.taught.id
            )
            return None
        return ChatResponse(answer=match.taught.answer, citations=citations)

    def _remember(self, request: ChatRequest, assistant_answer: str | None) -> None:
        """Запоминает реплику пользователя и (если был) ответ ассистента."""
        self.sessions.append(request.session_id, "user", request.message)
        if assistant_answer:
            self.sessions.append(request.session_id, "assistant", assistant_answer)

    def _build_context(self, request: ChatRequest, hits, history=None) -> str:
        """Контекст для LLM — только релевантные фрагменты, а не статьи целиком.

        Замер на реальном корпусе: шесть статей закона — это ~28 000 символов
        (~9 400 токенов) сплошного юридического текста. Модель на 1.7b в таком
        объёме тонет и начинает выдумывать статьи (проверено: сочинила «ст. 19
        о временной нетрудоспособности» в ответ про пособие при рождении).
        Поэтому в промпт уходит выборка релевантных предложений, а цитата
        остаётся полной статьёй — принцип «в ответе дословный пункт акта»
        не нарушается, урезается только объём промпта.
        """
        blocks: list[str] = []
        if history:
            dialogue = "\n".join(
                f"{'Пользователь' if turn['role'] == 'user' else 'Ассистент'}: {turn['content']}"
                for turn in history
            )
            blocks.append(
                "Предыдущий диалог (учитывай его при ответе на уточнения):\n" + dialogue
            )
        for hit in hits:
            chunk = hit.chunk
            excerpt = best_excerpt(chunk.text, request.message)
            blocks.append(
                f"[{chunk.chunk_id}] {chunk.doc_title}"
                f" ({chunk.doc_number}, {chunk.doc_date})\n{excerpt}"
            )
        context = "\n\n".join(blocks)
        # Вопрос обязан быть в промпте явно. Раньше его там не было вообще:
        # уходили только история и фрагменты, и модель отвечала «в воздух» —
        # на вопрос, которого не видела в контексте.
        context = f"{context}\n\n=== ВОПРОС ПОЛЬЗОВАТЕЛЯ ===\n{request.message}"
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

    def _done(
        self,
        answer,
        citations,
        refusal=False,
        suggested=None,
        started=0.0,
        source="corpus",
    ) -> ChatResponse:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ChatResponse(
            answer=answer,
            citations=citations,
            refusal=refusal,
            suggested_services=suggested or [],
            latency_ms=latency_ms,
            source="refusal" if refusal else source,
        )
