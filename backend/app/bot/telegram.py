"""Telegram-бот (aiogram 3): текст + голосовые сообщения.

Токен пустой → бот не стартует, REST API работает независимо (см. config).
Голос: OGG/Opus → ffmpeg → faster-whisper → обычный RAG-пайплайн.
Ответ голосом (Silero) — неделя 3, сейчас текстом.
"""

import logging
import tempfile
from pathlib import Path

from app.chat.orchestrator import Orchestrator
from app.core.config import Settings
from app.models.schemas import ChatRequest
from app.voice.gateway import VoiceGateway

logger = logging.getLogger(__name__)


def _format_answer(answer: str, citations) -> str:
    lines = [answer, ""]
    if citations:
        lines.append("📖 Источники:")
        for i, citation in enumerate(citations, start=1):
            location = f", {citation.location}" if citation.location else ""
            lines.append(f"{i}. {citation.doc_title}{location}")
            if citation.url:
                lines.append(f"   {citation.url}")
    lines.append("")
    lines.append("ℹ️ «ДаРэчы» отвечает только по официальным актам. Подача — в орган соцзащиты.")
    return "\n".join(lines)


def build_bot(settings: Settings, orchestrator: Orchestrator, voice: VoiceGateway):
    """Собирает Dispatcher с роутерами. Возвращает None, если токена нет."""
    try:
        from aiogram import Bot, Dispatcher, F
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.filters import CommandStart
        from aiogram.types import Message
    except ImportError:
        return None

    if not settings.telegram_bot_token:
        return None

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(
            "Привет! Я «ДаРэчы» — ИИ-проводник по госуслугам Беларуси.\n"
            "Опишите ситуацию человеческим языком: «у нас родился ребёнок, "
            "что оформлять?» — отвечу с цитатами из официальных актов.\n\n"
            "Голосовые тоже понимаю 🎙"
        )

    async def _answer_and_reply(message: Message, question: str) -> None:
        response = orchestrator.answer(ChatRequest(message=question))
        await message.answer(_format_answer(response.answer, response.citations))
        # Голосовой дубль ответа (Silero). Любая неудача — молча остаёмся
        # на тексте: бот не должен ломаться из-за озвучки.
        try:
            audio = voice.synthesize_to_ogg(response.answer)
            if audio:
                import io

                await message.answer_voice(io.BytesIO(audio))
        except Exception:  # noqa: BLE001 — озвучка не критична
            logger.debug("TTS недоступен, отвечаем только текстом", exc_info=True)

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        if message.text:
            await _answer_and_reply(message, message.text)

    @dp.message(F.voice | F.audio)
    async def on_voice(message: Message) -> None:
        tg_file = message.voice or message.audio
        if tg_file is None:
            return
        status = await message.answer("🎙 Слушаю…")
        try:
            tg_file_obj = await bot.get_file(tg_file.file_id)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as fh:
                tmp = Path(fh.name)
                await bot.download_file(tg_file_obj.file_path, destination=fh)
            try:
                text = voice.transcribe_file(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            if not text:
                await status.edit_text("Не расслышал. Напишите текстом, пожалуйста.")
                return
            await status.edit_text(f"❓ {text}")
            await _answer_and_reply(message, text)
        except RuntimeError as exc:
            await status.edit_text(f"Голос пока недоступен: {exc}")

    return bot, dp
