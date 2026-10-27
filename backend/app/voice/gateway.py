"""Голосовой шлюз: STT (faster-whisper) + TTS (Silero). Неделя 3.

Тяжёлые зависимости импортируем лениво: API и тесты работают без них.
ffmpeg нужен в системе для конвертации OGG/Opus (Telegram) и webm (портал).
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _ffmpeg_to_wav(src: Path, dst: Path) -> None:
    subprocess.run(  # noqa: S603 — фиксированные аргументы, без пользовательского шелла
        ["ffmpeg", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(dst)],
        check=True,
        capture_output=True,
    )


class VoiceGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stt = None
        self._tts = None

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def tts_available(self) -> bool:
        """TTS готов, если есть ffmpeg и пакет torch (модель грузится лениво)."""
        if not self.available():
            return False
        try:
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def _load_stt(self):
        if self._stt is None:
            from faster_whisper import WhisperModel

            self._stt = WhisperModel(
                self.settings.whisper_model, device="cpu", compute_type="int8"
            )
        return self._stt

    def transcribe_file(self, audio_path: Path, language: str | None = None) -> str:
        """OGG/webm/wav → текст. Бросает RuntimeError, если окружение не готово."""
        if not self.available():
            msg = "ffmpeg не найден в PATH — голосовой шлюз недоступен"
            raise RuntimeError(msg)
        model = self._load_stt()
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            _ffmpeg_to_wav(audio_path, wav)
            segments, _info = model.transcribe(str(wav), language=language)
            return " ".join(segment.text.strip() for segment in segments).strip()

    def synthesize_to_ogg(self, text: str, lang: str | None = None) -> bytes:
        """Текст ответа → OGG/Opus. RuntimeError — окружение не готово,
        ValueError — язык не поддержан. Вызывающий код деградирует в текст."""
        from app.voice.tts import SileroTts, wav_to_ogg_opus

        if not self.tts_available():
            msg = "TTS недоступен: нет ffmpeg или torch"
            raise RuntimeError(msg)
        if self._tts is None:
            self._tts = SileroTts()
        wav = self._tts.synthesize_wav(text, lang=lang or self.settings.tts_lang)
        return wav_to_ogg_opus(wav)
