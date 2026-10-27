"""TTS (Silero v4): текст → речь → ogg/opus для Telegram и портала.

Ленивая загрузка: torch и модель подгружаются при первом синтезе, поэтому
API, бот и тесты работают без них (деградация — ответ только текстом).

Silero отдаёт ru-голоса; белорусского голоса в открытых моделях достойного
качества нет — это честное ограничение MVP (см. docs/план-разработки.md).
"""

import re
import subprocess
import tempfile
from pathlib import Path

# Сокращения, которые Silero прочитает как мусор («пэ точка»), — расшифровываем.
_SPEECH_SUBS = [
    (re.compile(r"\bст\.\s*", re.IGNORECASE), "статья "),
    (re.compile(r"\bп\.\s*(?=\d)", re.IGNORECASE), "пункт "),
    (re.compile(r"\bРБ\b"), "Республика Беларусь"),
    (re.compile(r"\bтыс\.\s*", re.IGNORECASE), "тысяч "),
    (re.compile(r"\bг\.\s*(?=\d{4})", re.IGNORECASE), "год "),
    # URL в речи не читаем — пользователь видит их в тексте ответа.
    (re.compile(r"https?://\S+"), "ссылка в тексте сообщения"),
    (re.compile(r"[#*_`>|]"), ""),
]


def speech_text(text: str) -> str:
    """Приводит текст ответа к удобочитаемой для синтеза форме."""
    result = text
    for pattern, replacement in _SPEECH_SUBS:
        result = pattern.sub(replacement, result)
    return re.sub(r"\s+", " ", result).strip()


class SileroTts:
    """Обёртка над Silero v4. Тяжёлые импорты — ленивые, при первом синтезе."""

    def __init__(self, speaker: str = "baya") -> None:
        self.speaker = speaker
        self._model = None

    def _load(self):
        if self._model is None:
            import torch  # noqa: F401 — нужен torch.hub для загрузки модели

            self._model, _sample_rate = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language="ru",
                speaker="v4_ru",
                source="github",
                trust_repo=True,  # официальный репозиторий Silero, автоматическое доверие
            )
            self._model.to(torch.device("cpu"))
        return self._model

    def synthesize_wav(self, text: str, lang: str = "ru") -> bytes:
        """Текст → WAV (24 kHz mono). Бросает RuntimeError, если окружения нет."""
        if lang != "ru":
            msg = "TTS: в MVP только русские голоса (be — этап 2)"
            raise ValueError(msg)
        clean = speech_text(text)
        if not clean:
            return b""
        model = self._load()
        try:
            audio = model.apply_tts(
                text=clean, speaker=self.speaker, sample_rate=24000
            )
        except Exception as exc:  # torch.hub ошибки разнородны — оборачиваем
            msg = f"TTS-синтез не удался: {exc}"
            raise RuntimeError(msg) from exc
        import io
        import wave

        samples = audio.numpy()  # float32, диапазон [-1, 1]
        pcm = (samples * 32767).astype("int16").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(24000)
            fh.writeframes(pcm)
        return buf.getvalue()


def wav_to_ogg_opus(wav: bytes) -> bytes:
    """WAV → OGG/Opus (формат голосовых сообщений Telegram, компактный для веба)."""
    if not wav:
        return b""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.ogg"
        src.write_bytes(wav)
        subprocess.run(  # noqa: S603 — фиксированные аргументы, без пользовательского шелла
            [
                "ffmpeg", "-y", "-i", str(src),
                "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(dst),
            ],
            check=True, capture_output=True,
        )
        return dst.read_bytes()
