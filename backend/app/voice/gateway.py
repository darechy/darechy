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

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None

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


def synthesize(text: str, language: str = "ru") -> Path:
    """TTS → путь к ogg-файлу. Неделя 3; сейчас — NotImplementedError-заглушка
    с ясным сообщением (бот в этом случае шлёт только текст)."""
    msg = "TTS появится в неделе 3 (Silero v4): ответ идёт текстом"
    raise NotImplementedError(msg)
