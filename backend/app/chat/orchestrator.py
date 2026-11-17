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
from app.services.telemetry import measured, stage

if TYPE_CHECKING:  # импорт только для аннотаций: taught.py сам импортирует схемы
    from app.chat.taught import TaughtStore

logger = logging.getLogger(__name__)

# Вопросы про документы («какие документы нужны», «что взять с собой») без
# указания услуги. Корпус умеет отвечать на них только в привязке к услуге:
# без неё поиск тянет документы из подходящих по словам процедур, и модель
# честно пересказывает не тот пункт. Реальный случай: «какие документы нужны
# для получения паспорта» отвечало документами на пособие при рождении.
DOCS_TEMPLATE = (
    "Для услуги «{title}» нужны:\n{items}\n\n"
    "{authority_line}{cost_line}Источник: {source}."
)

CLARIFY_TEMPLATE = (
    "Уточните, пожалуйста, для какой услуги нужны документы — от этого зависит "
    "перечень. Я подскажу по этим услугам: {services}. "
    "Если вашей ситуации в списке нет — скажите своими словами, я поищу в базе "
    "нормативных актов."
)

_DOC_INTENT_TOKENS = {
    "документ", "документы", "справка", "справки", "бумаги", "перечень",
    "заявление", "свидетельство", "копия", "копии",
}
_DOCUMENT_REQUIREMENT = re.compile(r"\b(?:нужн\w*|обязательн\w*|потребу\w*)\s+ли\b", re.IGNORECASE)
_CARE_AT_HOME = re.compile(r"\b(?:декрет\w*|дома|сижу|сидим|сидит)\b", re.IGNORECASE)
_YOUNG_CHILD = re.compile(r"\b(?:малыш\w*|маленьк\w+\s+реб[её]н\w*|реб[её]н\w*\s+до тр[её]х)\b", re.IGNORECASE)
_SUPPORT_REQUEST = re.compile(r"\b(?:рассчитыва\w*|помощ\w*|поддерж\w*|выплат\w*|плат\w*|деньг\w*|положен\w*)\b", re.IGNORECASE)
_BENEFIT = re.compile(r"\b(?:пособи|выплат)\w*\b", re.IGNORECASE)
_CARE_ELIGIBILITY = re.compile(r"\b(?:кому (?:назнач|положен)\w+|кто (?:имеет|может))\b.*\bпо уходу\b", re.IGNORECASE)
_DEADLINE_WORDS = re.compile(r"\b(?:срок\w*|когда|до как\w+ дат\w*)\b", re.IGNORECASE)
_ELAPSED_TIME = re.compile(r"\b(?:недел|месяц|дней|год)\w*\b.{0,30}\bпосле\b|\bпрошл[ои]\b", re.IGNORECASE)
_TITLE_LABEL = re.compile(r"^(?:статья|пункт|позиция)\s+[\d.-]+\s*|\s*\(повтор \d+\)$", re.IGNORECASE)
_APPLY_WORDS = re.compile(r"\b(?:обрат\w+|обращ\w+|подат\w+|подач\w+)\b", re.IGNORECASE)
_APPLICATION_AUTHORITY = re.compile(
    r"\b(?:где (?:назнач|оформ)\w+|куда (?:идти|обрат\w+)|к кому идти)\b", re.IGNORECASE
)
_EARLY_REGISTRATION = re.compile(
    r"\b(?:ранн\w+|(?:[4-9]|1[01]|восьм\w+|девят\w+)\s+недел\w*)\b.{0,60}\bуч[её]т"
    r"|\bуч[её]т\w*\b.{0,50}\b(?:рано|ранн\w*|до 12 недель)\b", re.IGNORECASE
)
_BIRTH_BENEFIT = re.compile(r"\b(?:единовременн\w*|разов\w*)\b.{0,45}\bрожден\w*", re.IGNORECASE)
_DISABILITY_BENEFIT_AMOUNT = re.compile(
    r"\b(?:размер\w*|сколько)\b.{0,70}\b(?:реб[её]н\w*[- ]инвалид\w*|реб[её]н\w*\s+с инвалидност\w*)"
    r".{0,30}\bдо (?:18|восемнадцати) лет\b", re.IGNORECASE
)
_BENEFIT_ACT = "2012_7-Z_zakon-o-gosposobiyakh"
_OUTSIDE_SCOPE = re.compile(
    r"^\s*(?:напиши|сочини|придумай)\s+(?:мне\s+)?(?:сти[хш]\w*|песню|анекдот)"
    r"|\b(?:паспорт\w*|гражданств\w*)\b.{0,40}\bв России\b",
    re.IGNORECASE,
)

# Связываем бытовую лексику с формулировками каталога и заголовков актов.
_SEARCH_PHRASES = tuple(
    (re.compile(pattern, re.IGNORECASE), phrase)
    for pattern, phrase in (
        (r"\bвыписал\w*\s+из роддома\b|\bвыплат\w*\b.{0,35}\bрожден\w*", "единовременное пособие в связи с рождением ребенка"),
        (r"\bна (?:уч[её]т\w*.{0,35}(?:недел|рано)|(?:[4-9]|1[01]|ранн\w+|восьм\w+|девят\w+)\s+недел\w*.{0,55}уч[её]т)", "пособие женщинам ставшим на учет до 12 недель беременности"),
        (r"\bобжал\w*|\bоспор\w*\b", "обжалование решений по назначению государственных пособий"),
        (r"\bпрекраща\w+\s+(?:плат|выплач)\w+", "прекращение выплаты государственных пособий"),
        (r"\bВИЧ\b", "ребенок инфицированный вирусом иммунодефицита человека"),
        (r"\bсколько времени\b.{0,55}\bбеременност\w+\b", "период на который назначается пособие по беременности и родам"),
    )
)

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
    " (5) начни с прямого ответа простыми словами. Если вопрос общий или "
    "ответ зависит от ситуации, задай один конкретный уточняющий вопрос. "
    "Не переписывай весь фрагмент акта. Дословно цитируй только по просьбе."
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


_REFUSAL_MAX_CHARS = 300
# Проверка слов и чисел отсеивает неподтверждённые утверждения. Близость
# слов сама по себе не проверяет смысл, поэтому числа проверяются отдельно.
_MIN_SUPPORT_SHARE = 0.65
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_CLAIM_RE = re.compile(r"(?<=[.!?;])\s+(?=[А-ЯЁA-Z0-9])|\n+")


def _supporting_ids(answer: str, excerpts: dict[str, str]) -> list[str]:
    accepted: list[str] = []
    for line in _CLAIM_RE.split(answer):
        markers = list(dict.fromkeys(_extract_markers(line)))
        if any(marker not in excerpts for marker in markers):
            return []
        claim = _strip_markers(line)
        claim_tokens = set(tokenize(claim))
        if not claim_tokens:
            continue
        candidates = markers or list(excerpts)
        numbers = set(_NUMBER_RE.findall(claim))
        supported = []
        for chunk_id in candidates:
            evidence = excerpts[chunk_id]
            share = len(claim_tokens & set(tokenize(evidence))) / len(claim_tokens)
            if share >= _MIN_SUPPORT_SHARE and numbers <= set(_NUMBER_RE.findall(evidence)):
                supported.append(chunk_id)
        if not supported or (markers and len(supported) != len(markers)):
            return []
        # При отсутствии маркера выбираем первый фрагмент с текстовой опорой.
        for chunk_id in supported if markers else supported[:1]:
            if chunk_id not in accepted:
                accepted.append(chunk_id)
    return accepted


def _looks_like_refusal(answer: str) -> bool:
    """Отказ своими словами, а не только словом ОТКАЗ."""
    lowered = answer.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


_ARTICLE_MENTION_RE = re.compile(
    r"(?:стать[ияеюё]й?|ст\.)\s*№?\s*(\d+(?:-\d+)?)", re.IGNORECASE
)


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
    # Маркеры здесь НЕ трогаем: они нужны дальше для проверки ссылок.
    # Убирает их финальный _strip_markers — уже для показа пользователю.
    content = answer.replace("ОТКАЗ", " ")
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

    @measured
    def answer(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        message = request.message.casefold()
        distress = bool(re.search(
            r"\b(?:умер(?:ла|ли)?|погиб\w*|скончал\w*|сдох\w*|"
            r"потерял\w*|уволили|развод\w*|страшно|одиноко|"
            r"тяжело|горе|заболел\w*|насили\w*)\b", message
        ))
        personal = bool(re.search(r"\b(?:я|мне|меня|мой|моя|моего|моей|мы|нас|наш\w*)\b", message))
        needs_support = distress and personal
        official = request.service_id or re.search(
            r"\b(?:пособ\w*|выплат\w*|документ\w*|справк\w*|"
            r"заявлен\w*|закон\w*|стать\w*|оформ\w*|наслед\w*|"
            r"пенси\w*|компенсац\w*|регистр\w*|курс доллара|курс евро)\b", message
        )
        casual = re.match(
            r"^(?:давай (?:поболтаем|поговорим)|расскажи (?:шутку|анекдот)|"
            r"(?:напиши|сочини|придумай) (?:мне )?(?:стих\w*|песню|анекдот|короткое имя|кличку))\b",
            message.strip(),
        )
        if casual or (needs_support and not official):
            return self._conversation(request, started, needs_support)
        response = self._answer(request)
        if response.refusal and not official and not _OUTSIDE_SCOPE.search(message):
            recent = self.sessions.history(request.session_id)
            # An earlier grounded answer can make a brief follow-up domain-specific.
            if not any(turn["role"] == "assistant" for turn in recent):
                return self._conversation(request, started)
        if needs_support:
            response.answer = "Мне жаль, что вам приходится через это проходить.\n\n" + response.answer
        return response

    def _conversation(self, request: ChatRequest, started: float, supportive: bool = False) -> ChatResponse:
        stage("conversation")
        history = self.sessions.history(request.session_id)
        context = "\n".join(
            f"{turn['role']}: {turn['content'][:1000]}" for turn in history[-4:]
        )
        prompt = (
            "Ты Veda V-4. Отвечай естественно на языке собеседника. "
            "Если человек просит поговорить, поддержи разговор. Если просьба понятна, "
            "помоги сразу. Если нет — задай один короткий вопрос. "
            "Не выдумывай законы, контакты и результаты поиска. "
            "В вопросах о правах, выплатах, лечении или деньгах не давай конкретных "
            "рекомендаций без проверенных данных."
        )
        if request.effort == "quick":
            prompt += " Ответь максимум двумя короткими предложениями."
        if supportive:
            prompt += " Начни с короткого сочувствия и не давай непрошеных советов."
        answer = self.llm.chat(
            prompt,
            f"Предыдущие реплики:\n{context}\nТекущее сообщение:\n{request.message}",
            max_tokens={"quick": 80, "standard": 150, "deep": 220}[request.effort],
        )
        if not answer:
            answer = (
                "Мне жаль, что вам сейчас тяжело. Что помогло бы вам в этот момент?"
                if supportive else
                "Сейчас не получилось сформулировать ответ. Можете уточнить, что для вас главное?"
            )
        self._remember(request, answer)
        return self._done(answer, [], started=started, source="conversation")

    def _answer(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        message = re.sub(r"[\s!?.,]+", " ", request.message.casefold()).strip()
        if message in {"привет", "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "прывітанне"}:
            return self._done("Здравствуйте! Какую госуслугу хотите оформить?", [],
                              started=started, source="greeting")
        if message in {"спасибо", "благодарю", "дзякуй"}:
            return self._done("Пожалуйста!", [], started=started, source="greeting")
        if message in {"что ты умеешь", "чем можешь помочь", "кто ты", "помощь"}:
            return self._done("Помогаю разобраться с госуслугами Беларуси: найти нужную услугу, "
                              "узнать, какие документы собрать и куда обращаться.", [],
                              started=started, source="greeting")
        if message in {"как дела", "привет как дела", "как ты", "как жизнь", "что нового"}:
            return self._done("Всё хорошо, спасибо! А у вас как?", [], started=started, source="greeting")
        if re.match(r"^(?:(?:можешь|пожалуйста) )?(?:напиши|сделай|создай|сгенерируй|написать) (?:мне )?(?:скрипт|код|программу)\b", message):
            return self._done("Скрипты и программы я не пишу. Могу помочь разобраться с госуслугами.",
                              [], refusal=True, started=started)
        if re.fullmatch(r"(?:я )?(?:хочу |хотим |как )?оформить пособие на реб[её]нка", message):
            self._remember(request, None)
            return self._done("Какое пособие хотите оформить — разовое при рождении ребёнка "
                              "или ежемесячное по уходу? Если не уверены, расскажите, сколько лет ребёнку.",
                              [], started=started, source="clarify")
        if _OUTSIDE_SCOPE.search(request.message):
            return self._done("С этим запросом я не помогу. Могу подсказать по госуслугам Беларуси.", [], refusal=True, started=started)
        if (
            re.search(r"\b(?:родил(?:ся|ась)|рождени\w*)\b", message)
            and re.search(r"\b(?:какие|что)\b", message)
            and re.search(r"\b(?:пособи|выплат)\w*\b", message)
            and not (set(tokenize(message)) & _DOC_INTENT_TOKENS)
            and not re.search(r"\bиз документ\w+\b", message)
            and not _BIRTH_BENEFIT.search(message)
            and not request.service_id
        ):
            benefits = [
                ("posobie-rozhdenie", "Разовое пособие при рождении ребёнка", "st-11_п-2"),
                ("posobie-uhod-do-3-let", "Ежемесячное пособие по уходу до трёх лет", "st-13_п-2"),
                ("posobie-12-nedel", "Разовое пособие при постановке на учёт до 12 недель беременности", "st-10_п-1"),
            ]
            lines, citations, services = [], [], []
            for service_id, title, point in benefits:
                sources = _citations_from([f"{_BENEFIT_ACT}_{point}"], self.store)
                if sources:
                    lines.append(f"• {title}.")
                    citations.extend(sources)
                    services.append(service_id)
            if lines:
                answer = "После рождения ребёнка проверьте условия этих выплат:\n\n" + "\n".join(lines)
                answer += "\n\nМогу подсказать, какие документы нужны и куда подать заявление."
                self._remember(request, answer)
                return self._done(answer, citations, suggested=services, started=started, source="extractive")
        history = self.sessions.history(request.session_id)
        query = request.message
        if all(pattern.search(query) for pattern in (_CARE_AT_HOME, _YOUNG_CHILD, _SUPPORT_REQUEST)):
            query += " ежемесячное пособие по уходу за ребенком до 3 лет"
        if _BENEFIT.search(request.message) and not _APPLICATION_AUTHORITY.search(request.message):
            query = " ".join([query, *(phrase for pattern, phrase in _SEARCH_PHRASES if pattern.search(request.message))])

        # Маршрут считается ПЕРВЫМ: он нужен и слою доучивания (выученный ответ,
        # привязанный к услуге, уместен только если вопрос — об этой услуге),
        # и скоупу поиска.
        route_started = time.perf_counter()
        routed, routed_by = self._route(query)
        route_ms = int((time.perf_counter() - route_started) * 1000)
        if not request.service_id and _SUPPORT_REQUEST.search(message):
            if _EARLY_REGISTRATION.search(message) and re.search(r"\bбеременн\w*\b", message):
                routed = ["posobie-12-nedel"]
            elif re.search(r"\bза перв\w+ реб[её]н\w*\b", message):
                routed = ["posobie-rozhdenie"]
        suggested = routed
        prefer_ids = (
            self.router.scope_chunk_ids([request.service_id])
            if self.router and self.settings.service_scoping and request.service_id
            else set()
        )

        # Слой доучивания: ответ, подтверждённый администратором, важнее
        # генерации. Отвечает мгновенно и не зависит от качества модели, но
        # цитаты всё равно берутся из корпуса — не из текста администратора.
        taught = self._taught_answer(request, routed)
        if taught is not None:
            self._remember(request, taught.answer)
            return self._done(
                taught.answer,
                taught.citations,
                suggested=suggested,
                started=started,
                source="taught",
            )

        # Вопрос про документы и услуга распознана — отвечаем списком из
        # карточки услуги, а не пересказом модели. Список взят из перечня
        # административных процедур дословно, он же показан на странице
        # услуги: ответ ассистента и портал не могут расходиться.
        # Замер на живом сервере: на вопрос «какие документы нужны для
        # регистрации брака» модель отдала одну строку из списка вместо всего
        # перечня документов.
        documents_answer = self._documents_answer(request, routed)
        if documents_answer is not None:
            self._remember(request, documents_answer.answer)
            return self._done(
                documents_answer.answer,
                documents_answer.citations,
                suggested=suggested,
                started=started,
                source="checklist",
            )

        # Интент «какие документы» без распознанной услуги: уточняем, а не
        # угадываем. Иначе ответ будет не про тот документ, который спросили.
        if self._needs_clarification(request, routed, history):
            answer = CLARIFY_TEMPLATE.format(services=self._service_titles())
            self._remember(request, answer)
            return self._done(
                answer, [], suggested=suggested, started=started, source="clarify"
            )

        # Для однозначных вопросов о конкретной норме выводим весь пункт:
        # сокращение может потерять исключения, условия или часть ставок.
        if not history and not prefer_ids and (_BENEFIT.search(request.message) or _SUPPORT_REQUEST.search(request.message)):
            point = self._direct_benefit_point(request.message)
            hit = self.store.get(f"{_BENEFIT_ACT}_{point}") if point else None
            if hit is not None:
                return self._answer_with_quote(request, hit, query, suggested, started, full=True)

        # Уточняющие вопросы («а какие документы?») теряют смысл без контекста:
        # в поисковый запрос подмешиваем предыдущую реплику пользователя.
        if _BENEFIT.search(query) and _APPLY_WORDS.search(query) and (
            _DEADLINE_WORDS.search(query) or _ELAPSED_TIME.search(query)
        ):
            query += " сроки обращения за назначением государственных пособий"
        elif _BENEFIT.search(query) and _APPLICATION_AUTHORITY.search(query):
            query += " основные положения назначение и выплата государственных пособий"
        elif _BENEFIT.search(query) and _CARE_ELIGIBILITY.search(query):
            query += " право на пособие по уходу за ребенком в возрасте до 3 лет"
        if history:
            last_user = next(
                (turn["content"] for turn in reversed(history) if turn["role"] == "user"),
                "",
            )
            if last_user and request.message.lower().startswith(
                ("а ", "и ", "а какие", "а что", "а где", "а когда", "а сколько")
            ):
                query = f"{last_user} {query}"

        # Guardrail №1: нет релевантных чанков → отказ. Порог применяется
        # к сырым скорам плеч внутри store.search (RRF-скор — ранговый).
        stage("retrieval")
        lexical_evidence = []
        if not history and not prefer_ids and not self._is_document_requirement(request.message):
            lexical = self.store.search_bm25(query, top_k=1)
            if lexical:
                title_words = set(tokenize(_TITLE_LABEL.sub("", lexical[0].chunk.title or "")))
                overlap = title_words & set(tokenize(query))
                if len(overlap) >= 3 and len(overlap) >= 0.67 * len(title_words):
                    lexical_evidence = lexical
                if lexical_evidence and len(overlap) == len(title_words):
                    return self._answer_with_quote(request, lexical_evidence[0], query, suggested, started)
        search_started = time.perf_counter()
        hits = self.store.search(
            query,
            self.llm,
            top_k=request.top_k,
            score_threshold=self.settings.retrieval_score_threshold,
            prefer_chunk_ids=prefer_ids or None,
            group_articles=self.settings.group_articles,
        )
        search_ms = int((time.perf_counter() - search_started) * 1000)
        if not hits:
            if not routed and not history and not request.service_id and len(message.split()) <= 2:
                return self._done("Не понял, что вы имеете в виду. Напишите, что хотите оформить "
                                  "или узнать.", [], started=started, source="clarify")
            self._remember(request, None)
            return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                              suggested=suggested, started=started)

        stage("generation")
        llm_started = time.perf_counter()
        excerpts = {hit.chunk.chunk_id: best_excerpt(hit.chunk.text, query) for hit in hits}
        budget = {"quick": 80, "standard": None, "deep": 320}[request.effort]
        prompt = SYSTEM_PROMPT
        if request.effort == "quick":
            prompt += "\nДай прямой ответ в одном-двух коротких предложениях."
        elif request.effort == "standard":
            prompt += "\nОбъясни главное простыми словами и подскажи ближайший шаг."
        elif request.effort == "deep":
            prompt += ("\nДай краткий вывод, затем объясни, как условия и исключения "
                       "в приведённых нормах влияют на вывод. На каждый шаг дай ссылку. "
                       "Покажи только объяснение по источникам, без черновика рассуждений.")

        context = self._build_context(request, hits, excerpts, history)
        llm_answer = (
            self.llm.chat(prompt, context, max_tokens=budget)
            if budget else self.llm.chat(prompt, context)
        )
        llm_ms = int((time.perf_counter() - llm_started) * 1000)
        logger.info(
            "Маршрут: %s (%s, %d мс) | поиск: %d чанков (%d мс) | модель: %d мс",
            routed or "—", routed_by, route_ms, len(hits), search_ms, llm_ms,
        )

        if not llm_answer or not llm_answer.strip():
            best = hits[0]
            keywords = set(tokenize(query))
            evidence = set(tokenize(f"{best.chunk.title or ''} {best.chunk.text}"))
            if self._is_document_requirement(request.message) or (
                best.via == "bm25" and len(keywords) > 2 and len(keywords & evidence) < 2
            ):
                self._remember(request, None)
                return self._done(REFUSAL_TEMPLATE, [], refusal=True, suggested=suggested, started=started)
            # Guardrail №2: LLM недоступна — экстрактивный режим, не выдумываем.
            return self._answer_with_quote(request, hits[0], query, suggested, started)

        if _looks_like_refusal(llm_answer):
            # Единая политика: короткая реплика с признаком отказа и без
            # подтверждённой ссылки — это отказ, а не ответ. Раньше текст
            # «во фрагментах нет информации» уходил пользователю как обычный
            # ответ с автоматически приклеенной цитатой.
            content = _strip_refusal(llm_answer)
            has_marker = any(marker in excerpts for marker in _extract_markers(llm_answer))
            if not content or (len(content) < _REFUSAL_MAX_CHARS and not has_marker):
                if lexical_evidence:
                    return self._answer_with_quote(request, lexical_evidence[0], query, suggested, started)
                self._remember(request, None)
                return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                                  suggested=suggested, started=started)
            llm_answer = content

        marker_ids = (
            [] if _mentions_unknown_article(llm_answer, hits) else _supporting_ids(llm_answer, excerpts)
        )
        citations = _citations_from(marker_ids, self.store)
        if not citations or len(citations) != len(marker_ids):
            if lexical_evidence:
                return self._answer_with_quote(request, lexical_evidence[0], query, suggested, started)
            self._remember(request, None)
            return self._done(REFUSAL_TEMPLATE, [], refusal=True,
                              suggested=suggested, started=started)

        answer = _strip_markers(llm_answer)
        self._remember(request, answer)
        return self._done(
            answer, citations, refusal=False,
            suggested=suggested, started=started,
        )

    # --- внутреннее ---------------------------------------------------------

    def _direct_benefit_point(self, message: str) -> str | None:
        if self._is_document_requirement(message):
            return None
        if re.search(r"\bвич\b", message, re.IGNORECASE) and re.search(
            r"\b(?:реб[её]н\w*|дет\w*)\b", message, re.IGNORECASE
        ):
            return "st-17_п-1"
        if re.search(r"\bза перв\w+ реб[её]н\w*\b", message, re.IGNORECASE):
            return "st-11_п-1"
        if _DISABILITY_BENEFIT_AMOUNT.search(message):
            return "st-18_п-5"
        if _APPLICATION_AUTHORITY.search(message) and _BIRTH_BENEFIT.search(message):
            return "st-21_п-3"
        if _EARLY_REGISTRATION.search(message) and _SUPPORT_REQUEST.search(message):
            return "st-10_п-1"
        return None

    def _documents_answer(self, request: ChatRequest, routed: list[str]) -> ChatResponse | None:
        """Собирает ответ про документы из карточки услуги. None — не наш случай."""
        if not routed or self.router is None or not self.settings.checklist_answers:
            return None
        if not (set(tokenize(request.message)) & _DOC_INTENT_TOKENS) and not re.search(
            r"\bиз документ\w+\b", request.message, re.IGNORECASE
        ):
            return None
        if self._is_document_requirement(request.message) or _APPLICATION_AUTHORITY.search(request.message):
            return None

        services = {service.id: service for service in getattr(self.router, "_services", [])}
        service = services.get(routed[0])
        if service is None or not service.checklist:
            return None

        items = "\n".join(f"• {item.item}" for item in service.checklist)
        authority_line = f"Куда обращаться: {service.authority}.\n" if service.authority else ""
        cost_line = f"Стоимость: {service.cost}.\n" if service.cost else ""
        source = next((s.title for s in service.sources), "нормативный акт")
        answer = DOCS_TEMPLATE.format(
            title=service.title,
            items=items,
            authority_line=authority_line,
            cost_line=cost_line,
            source=source,
        )

        # Цитата — тот самый пункт перечня, из которого собран чек-лист.
        chunk_ids = [s.point for s in service.sources if s.point]
        citations = _citations_from(
            [cid for cid in chunk_ids if self.store.get(cid)], self.store
        )
        if not citations:
            return None  # нет подтверждённой нормы — пусть работает обычный путь
        return ChatResponse(answer=answer, citations=citations)

    @staticmethod
    def _is_document_requirement(message: str) -> bool:
        return bool(_DOCUMENT_REQUIREMENT.search(message) and re.search(
            r"\b(?:документ|справк|бумаг|переч|заявлен|свидетельств|копи)\w*", message, re.IGNORECASE
        ))

    def _service_titles(self) -> str:
        """Человеческий список услуг для уточняющего вопроса."""
        if self.router is None:
            return "государственные пособия семьям с детьми"
        titles = [
            service.title.lower()
            for service in getattr(self.router, "_services", [])[:5]
        ]
        return "; ".join(titles) if titles else "государственные пособия семьям с детьми"

    def _needs_clarification(
        self, request: ChatRequest, routed: list[str], history: list[dict] | None = None
    ) -> bool:
        """Просим уточнить услугу, если спрашивают про документы без привязки.

        Услуга со страницы (`service_id`), распознанный маршрут или предыдущий
        диалог снимают вопрос: тогда мы уже знаем, о какой услуге речь.
        Переспрашивать «а какие документы?» после разговора о пособии — глупо.
        """
        if routed:
            return False
        if history:
            return False
        if self.settings and not getattr(self.settings, "clarify_doc_intent", True):
            return False
        if getattr(self, "taught", None) is not None and self.taught.match(
            request.message, routed_services=routed
        ):
            return False  # выученный ответ знает, о чём речь
        tokens = set(tokenize(request.message))
        return bool(tokens & _DOC_INTENT_TOKENS)

    def _route(self, message: str) -> tuple[list[str], str]:
        """Маршрут услуги и кто решил. Нет роутера — пустой маршрут."""
        if self.router is None:
            return [], "none"
        route_with_ai = getattr(self.router, "route_with_ai", None)
        if route_with_ai is None:  # роутер-двойка в тестах без ИИ
            return self.router.route(message), "rules"
        if not self.settings.ai_router:
            return self.router.route(message), "rules"
        return route_with_ai(message)

    def _taught_answer(
        self, request: ChatRequest, routed: list[str] | None = None
    ) -> ChatResponse | None:
        """Проверяет слой доучивания. None — выученного ответа нет, идём искать."""
        if self.taught is None:
            return None
        match = self.taught.match(
            request.message,
            service_id=request.service_id,
            routed_services=routed,
        )
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
        self.sessions.append_exchange(request.session_id, request.message, assistant_answer)

    def _build_context(self, request: ChatRequest, hits, excerpts, history=None) -> str:
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
            excerpt = excerpts[chunk.chunk_id]
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

    def _answer_with_quote(self, request, best, query, suggested, started, *, full=False) -> ChatResponse:
        """Выводит фрагмент из корпуса, когда текст модели не нужен или не подтверждён."""
        citations = _citations_from([best.chunk.chunk_id], self.store)
        if not citations:
            self._remember(request, None)
            return self._done(REFUSAL_TEMPLATE, [], refusal=True, suggested=suggested, started=started)
        asks_for_quote = re.search(
            r"\b(?:процитируй|цитат\w*|покажи (?:норму|источник|статью)|дословно)\b",
            request.message, re.IGNORECASE,
        )
        text = best.chunk.text.strip()
        if not asks_for_quote and len(text) > _MAX_QUOTE_CHARS:
            service = next((
                service for service in getattr(self.router, "_services", [])
                if any(source.point == best.chunk.chunk_id for source in service.sources)
                and service.status != "черновик"
            ), None)
            if service:
                question = (
                    "Ребёнок родился в Беларуси или за границей? От этого зависит список документов."
                    if service.id == "posobie-rozhdenie" else
                    "Что хотите уточнить в первую очередь: документы, куда обращаться или сроки?"
                )
                parts = [service.summary]
                if request.effort != "quick" and service.authority:
                    parts.append(f"Куда обращаться: {service.authority}")
                if request.effort == "deep":
                    if service.deadline:
                        parts.append(f"Срок: {service.deadline}")
                    if service.cost:
                        parts.append(f"Стоимость: {service.cost}")
                parts.append(question)
                answer = "\n\n".join(parts)
                suggested = [service.id]
            else:
                title = best.chunk.title or best.chunk.doc_title
                answer = (
                    f"Нашёл норму по теме «{title}». "
                    "Что именно хотите уточнить: условия, порядок оформления или сроки?"
                )
        else:
            snippet = text if full and asks_for_quote else best_excerpt(
                text, query, max_chars=_MAX_QUOTE_CHARS
            )
            answer = snippet
        self._remember(request, answer)
        return self._done(answer, citations, suggested=suggested, started=started, source="extractive")

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
            suggested_services=[] if refusal else (suggested or []),
            latency_ms=latency_ms,
            source="refusal" if refusal else source,
        )
