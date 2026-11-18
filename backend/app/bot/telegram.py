"""Telegram-бот на aiogram. Запуск: python -m app.bot.telegram."""

import asyncio
import hashlib
import hmac
import html
import io
import logging
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
    User,
)
from aiohttp import ClientError
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.schemas import ChatRequest, ChatResponse, Citation

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_QUESTION_LENGTH = 4096
# Даже если все символы занимают два элемента UTF-16, сообщение укладывается в лимит.
MAX_REPLY_LENGTH = 2000
# Блок «Источники» делим по цитатам, чтобы не рвать HTML-теги посередине.
MAX_SOURCES_LENGTH = 3500
MAX_QUOTE_LENGTH = 350

BTN_EXAMPLES = "💡 Примеры вопросов"
BTN_ABOUT = "ℹ️ О боте"
MORE_DATA = "menu:examples"

EXAMPLES = (
    "Как получить справку о месте жительства?",
    "Какие документы нужны для регистрации брака?",
    "Как встать на учёт в детский сад?",
    "Как оформить пенсию по возрасту?",
    "Что делать при утере паспорта?",
    "Как получить справку о составе семьи?",
)

WELCOME_TEXT = (
    "👋 <b>Привет! Я «ДаРэчы»</b>\n\n"
    "Помогаю разобраться с государственными услугами Беларуси: какие документы нужны, "
    "куда обращаться, сколько стоит и в какие сроки.\n\n"
    "✨ <b>Как это работает</b>\n"
    "✍️ Напишите вопрос своими словами\n"
    "🎙 Или отправьте голосовое сообщение\n"
    "📚 Под ответом — источник: акт, пункт и цитата"
)
EXAMPLES_TEXT = "💡 <b>Попробуйте спросить</b>\nНажмите на вопрос или напишите свой:"
HELP_TEXT = (
    "ℹ️ <b>О боте «ДаРэчы»</b>\n\n"
    "Я отвечаю <b>только по официальным нормативным актам</b> и всегда показываю, "
    "откуда взята информация. Если подходящего источника нет, честно скажу об этом.\n\n"
    "✍️ Пишите вопросы текстом (до 4096 символов)\n"
    "🎙 Или присылайте голосовые (до 10 МБ) — я отвечу текстом и голосом\n"
    "💬 Можно уточнять: я помню контекст разговора\n\n"
    "⚠️ Ответы справочные: важные детали уточняйте в учреждении."
)
REFUSAL_TIP = "💡 Попробуйте переформулировать вопрос или уточните в соответствующем учреждении."
UNAVAILABLE_TEXT = "⚠️ <b>База знаний сейчас недоступна.</b>\nПопробуйте чуть позже."

BOT_DESCRIPTION = (
    "ДаРэчы — помощник по государственным услугам на базе ИИ Veda V-4.\n\n"
    "Спросите своими словами: какие документы нужны, куда обращаться, сколько стоит "
    "и в какие сроки. Ответ — по тексту нормативного акта, с указанием пункта.\n\n"
    "Можно писать или отправлять голосовые сообщения.\n\n"
    "Бот не заменяет консультацию специалиста: важные детали уточняйте в учреждении."
)
BOT_SHORT_DESCRIPTION = "ИИ-помощник по госуслугам Беларуси. Отвечает по тексту закона и показывает пункт."


class TelegramSettings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_api_key: str = ""
    telegram_api_base_url: str = "http://127.0.0.1:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AudioTooLarge(ValueError):
    pass


class AudioBuffer(io.BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_AUDIO_BYTES:
            raise AudioTooLarge
        return super().write(data)


class KnowledgeApi:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def chat(self, question: str, session_id: str) -> ChatResponse:
        body = ChatRequest(message=question, session_id=session_id)
        response = await self.client.post("/api/v1/chat", json=body.model_dump(exclude_none=True))
        response.raise_for_status()
        return ChatResponse.model_validate_json(response.content)

    async def transcribe(self, data: bytes, name: str, content_type: str) -> str:
        response = await self.client.post(
            "/api/v1/voice/transcribe", files={"file": (name, data, content_type)},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise ValueError("Поле text должно быть строкой")
        return payload["text"].strip()

    async def synthesize(self, text: str) -> bytes:
        response = await self.client.post("/api/v1/voice/synthesize", json={"text": text, "lang": "ru"})
        response.raise_for_status()
        return response.content


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _format_citation(index: int, citation: Citation) -> str:
    details = ", ".join(filter(None, (citation.doc_number, citation.doc_date, citation.location)))
    head = f"{index}. <b>{_esc(citation.doc_title)}</b>" + (f", {_esc(details)}" if details else "")
    lines = [head]
    if citation.quote:
        lines.append(f"<i>«{_esc(_clip(citation.quote, MAX_QUOTE_LENGTH))}»</i>")
    if citation.url and citation.url.startswith(("http://", "https://")):
        href = html.escape(citation.url, quote=True)
        lines.append(f'🔗 <a href="{href}">Открыть документ</a>')
    return "\n".join(lines)


def _sources_messages(citations: list[Citation]) -> list[str]:
    header = "📚 <b>Источники</b>"
    messages: list[str] = []
    current = header
    for index, citation in enumerate(citations, start=1):
        block = _format_citation(index, citation)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > MAX_SOURCES_LENGTH and current != header:
            messages.append(current)
            current = block
        else:
            current = candidate
    messages.append(current)
    return messages


def _split_message(text: str) -> Iterator[str]:
    while len(text) > MAX_REPLY_LENGTH:
        split_at = text.rfind("\n", 0, MAX_REPLY_LENGTH)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, MAX_REPLY_LENGTH)
        if split_at <= 0:
            split_at = MAX_REPLY_LENGTH
        yield text[:split_at]
        text = text[split_at:].lstrip()
    if text:
        yield text


def _answer_messages(response: ChatResponse) -> list[str]:
    parts = list(_split_message(response.answer.strip())) or ["Не удалось подготовить ответ."]
    texts = [_esc(part) for part in parts]
    texts[0] = f"{'🤷' if response.refusal else '💬'} {texts[0]}"
    if response.refusal:
        texts[-1] += f"\n\n{REFUSAL_TIP}"
    return texts


def _examples_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❓ {question}", callback_data=f"q:{index}")]
        for index, question in enumerate(EXAMPLES)
    ])


def _more_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Другие вопросы", callback_data=MORE_DATA)],
    ])


def _menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_EXAMPLES), KeyboardButton(text=BTN_ABOUT)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Спросите о госуслуге…",
    )


async def _send(message: Message, text: str, **kwargs) -> None:
    await message.answer(text, parse_mode=ParseMode.HTML, **kwargs)


@asynccontextmanager
async def _typing(bot: Bot, message: Message) -> AsyncIterator[None]:
    """Показывает «печатает…», пока модель готовит ответ (Telegram гасит статус через 5 с)."""
    chat_id = message.chat.id
    thread_id = message.message_thread_id if message.is_topic_message else None

    async def ping() -> None:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING, message_thread_id=thread_id)
        except TelegramAPIError:
            logger.debug("Не удалось показать «печатает…»")

    async def keep() -> None:
        while True:
            await asyncio.sleep(4.5)
            await ping()

    await ping()
    task = asyncio.create_task(keep())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_dispatcher(api: KnowledgeApi) -> Dispatcher:
    dp = Dispatcher()
    session_secret = secrets.token_bytes(32)

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        await _send(message, WELCOME_TEXT, reply_markup=_menu_markup())
        await _send(message, EXAMPLES_TEXT, reply_markup=_examples_markup())

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        await _send(message, HELP_TEXT)

    @dp.message(F.text == BTN_ABOUT)
    async def on_about(message: Message) -> None:
        await _send(message, HELP_TEXT)

    @dp.message(F.text == BTN_EXAMPLES)
    async def on_examples(message: Message) -> None:
        await _send(message, EXAMPLES_TEXT, reply_markup=_examples_markup())

    async def answer_question(
        message: Message,
        bot: Bot,
        question: str,
        *,
        user: User | None = None,
        voice_reply: bool = False,
    ) -> None:
        question = question.strip()
        if not question:
            return
        if len(question) > MAX_QUESTION_LENGTH:
            await _send(message, "✂️ Сократите вопрос до 4096 символов и отправьте его ещё раз.")
            return

        sender = user or message.from_user or message.sender_chat or message.chat
        identity = f"{message.chat.id}:{message.message_thread_id or 0}:{sender.id}"
        session_id = hmac.new(session_secret, identity.encode(), hashlib.sha256).hexdigest()
        try:
            async with _typing(bot, message):
                response = await api.chat(question, session_id)
        except (httpx.HTTPError, ValidationError):
            logger.warning("База знаний недоступна")
            await _send(message, UNAVAILABLE_TEXT)
            return

        outgoing: list[tuple[str, bool]] = [(text, False) for text in _answer_messages(response)]
        if response.citations:
            outgoing.extend((text, True) for text in _sources_messages(response.citations))
        for position, (text, is_sources) in enumerate(outgoing):
            extra: dict = {}
            if is_sources:
                extra["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
            if position == len(outgoing) - 1:
                extra["reply_markup"] = _more_markup()
            await _send(message, text, **extra)

        if voice_reply and response.answer and not response.refusal:
            try:
                audio = await api.synthesize(response.answer)
                if audio:
                    await message.answer_voice(BufferedInputFile(audio, filename="answer.ogg"))
            except (httpx.HTTPError, TelegramAPIError):
                logger.info("Озвучивание ответа недоступно")

    @dp.callback_query(F.data == MORE_DATA)
    async def on_more(callback: CallbackQuery) -> None:
        await callback.answer()
        if isinstance(callback.message, Message):
            await _send(callback.message, EXAMPLES_TEXT, reply_markup=_examples_markup())

    @dp.callback_query(F.data.startswith("q:"))
    async def on_example(callback: CallbackQuery, bot: Bot) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message) or not callback.data:
            return
        try:
            question = EXAMPLES[int(callback.data.split(":", 1)[1])]
        except (ValueError, IndexError):
            return
        await _send(message, f"❓ <b>{_esc(question)}</b>")
        await answer_question(message, bot, question, user=callback.from_user)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message, bot: Bot) -> None:
        if message.text:
            await answer_question(message, bot, message.text)

    @dp.message(F.voice | F.audio)
    async def on_voice(message: Message, bot: Bot) -> None:
        attachment = message.voice or message.audio
        if attachment is None:
            return
        too_large = "📦 Аудио должно быть не больше 10 МБ. Отправьте короткую запись или напишите вопрос."
        if (attachment.file_size or 0) > MAX_AUDIO_BYTES:
            await message.answer(too_large)
            return
        status = await message.answer("🎙 Распознаю запись…")
        try:
            remote = await bot.get_file(attachment.file_id)
            if remote.file_path is None:
                await status.edit_text("😕 Файл недоступен. Напишите вопрос текстом.")
                return
            with AudioBuffer() as buffer:
                await bot.download_file(remote.file_path, destination=buffer)
                suffix = remote.file_path.rsplit(".", 1)[-1].lower()
                if suffix == "oga":
                    suffix = "ogg"
                name = f"audio.{suffix}" if suffix.isalnum() and len(suffix) <= 8 else "audio.ogg"
                text = await api.transcribe(
                    buffer.getvalue(), name, attachment.mime_type or "application/octet-stream",
                )
        except AudioTooLarge:
            await status.edit_text(too_large)
            return
        except (httpx.HTTPError, TelegramAPIError, ClientError, TimeoutError, ValueError):
            await status.edit_text("😕 Не удалось распознать аудио. Напишите вопрос текстом.")
            return
        if not text:
            await status.edit_text("😕 На записи не удалось разобрать речь. Напишите вопрос текстом.")
            return
        await status.edit_text(
            f"🎙 <b>Вы сказали:</b> «{_esc(_clip(text, 500))}»\n⏳ Ищу ответ…", parse_mode=ParseMode.HTML,
        )
        await answer_question(message, bot, text, voice_reply=True)

    return dp


async def _setup_profile(bot: Bot) -> None:
    """Меню команд и описания бота. Ошибка здесь не должна мешать запуску."""
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Что я умею"),
        ])
        await bot.set_my_description(BOT_DESCRIPTION)
        await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    except TelegramAPIError:
        logger.warning("Не удалось обновить меню и описание бота", exc_info=True)


async def run(settings: TelegramSettings) -> None:
    if not settings.telegram_bot_token or not settings.telegram_api_key:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN и TELEGRAM_API_KEY в окружении или backend/.env.")
    base_url = httpx.URL(settings.telegram_api_base_url)
    if base_url.scheme not in {"http", "https"} or not base_url.host:
        raise SystemExit("TELEGRAM_API_BASE_URL должен быть полным HTTP(S) URL.")
    async with (
        Bot(token=settings.telegram_bot_token) as bot,
        httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {settings.telegram_api_key}"},
            timeout=httpx.Timeout(90, connect=5),
            trust_env=False,
        ) as client,
    ):
        await _setup_profile(bot)
        dispatcher = build_dispatcher(KnowledgeApi(client))
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(TelegramSettings()))
