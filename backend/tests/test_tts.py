"""TTS: нормализация текста под речь (без torch — чистая логика)."""

from app.voice.tts import speech_text


def test_abbreviations_expanded():
    assert "статья" in speech_text("см. ст. 5 акта")
    assert "пункт 3" in speech_text("по п. 3 норма")


def test_urls_removed():
    result = speech_text("См. https://pravo.by/document/123 подробности")
    assert "https://" not in result
    assert "подробности" in result


def test_markdown_stripped():
    assert "#" not in speech_text("## Заголовок\n**жирный** и `код`")
    assert "Заголовок" in speech_text("## Заголовок")


def test_whitespace_normalized():
    assert speech_text("а   б\n\n в") == "а б в"
