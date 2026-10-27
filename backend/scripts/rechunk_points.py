"""Разбор статей закона на чанки-пункты (требование kb_corpus/README.md: чанк = пункт).

Зачем: сейчас чанк = статья целиком, до 12 400 символов. Retrieval приносит
мусорные куски, а в промпт LLM уходит 9 400 токенов. Пункт — это то, чем
норма реально отвечает на вопрос.

Гарантия честности: разбор чисто механический. Текст не переписывается и не
дополняется — проверяется, что склейка пунктов точно равна исходной статье
(см. `verify_roundtrip`). Если текст не сходится, чанк не создаётся.

Запуск из backend/:  python -m scripts.rechunk_points [--write]
Без --write только отчёт, файлы не трогает.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import REPO_DIR  # noqa: E402

CORPUS = REPO_DIR / "kb_corpus" / "curated"

# Начало пункта: «1.», «2.1.», «3.1.1.», «5.1-1.» — но не «2012.» и не «ст. 11.».
_POINT_START = re.compile(r"(?:(?<=\s)|^)(\d{1,2}(?:\.\d{1,2}){0,3}(?:-\d)?)\.\s+(?=[А-ЯЁA-Z«\"(])")
_ARTICLE_HEAD = re.compile(r"^Статья\s+(\d+)\.\s*([^\n]*)")


def _is_next_point(previous: str, candidate: str) -> bool:
    """Проверяет, что candidate — следующий номер в последовательности.

    «Статья 11.» даёт кандидата «11», и без проверки он режет статью на огрызок
    «Статья» — так и появлялись 6-символьные чанки. Настоящая нумерация идёт
    по порядку: 1 → 2 → 2.1 → 2.2 → 3, где следующий номер либо увеличивает
    последнюю часть на единицу, либо наращивает подпункт.
    """
    prev = previous.split(".")
    cand = candidate.split(".")
    if len(cand) == len(prev) + 1 and cand[:-1] == prev:
        return True  # 1 → 1.1
    if len(cand) == len(prev) and cand[:-1] == prev[:-1]:
        try:
            return int(cand[-1]) == int(prev[-1]) + 1
        except ValueError:
            return False
    return False


def _valid_points(text: str) -> list[re.Match[str]]:
    """Отбирает только те номера, что образуют связную последовательность пунктов."""
    accepted: list[re.Match[str]] = []
    previous: str | None = None
    for match in _POINT_START.finditer(text):
        number = match.group(1)
        if number == "1" or (previous is not None and _is_next_point(previous, number)):
            accepted.append(match)
            previous = number
    return accepted


def split_points(text: str) -> list[tuple[str | None, str]]:
    """Возвращает [(номер пункта | None, текст)] — порядок исходный, текст дословный."""
    text = text.strip()
    matches = _valid_points(text)
    if not matches:
        return [(None, text)]

    pieces: list[tuple[str | None, str]] = []
    # Преамбула до первого пункта (заголовок статьи) — не чанк, уходит в title.
    head = text[: matches[0].start()].strip()
    if head:
        pieces.append((None, head))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pieces.append((match.group(1), text[match.start() : end].strip()))
    return pieces


def verify_roundtrip(pieces: list[tuple[str | None, str]], original: str) -> bool:
    """Склейка пунктов должна воспроизводить статью: ни один символ не потерян."""
    joined = re.sub(r"\s+", " ", " ".join(piece for _, piece in pieces)).strip()
    source = re.sub(r"\s+", " ", original).strip()
    return joined == source


def location_for(article_label: str, point: str | None) -> dict:
    return {"article": article_label, "point": f"пункт {point}" if point else None}


def rechunk_doc(path: Path) -> tuple[dict, list[str]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    new_chunks: list[dict] = []

    for chunk in doc["chunks"]:
        original = chunk["text"]
        pieces = split_points(original)
        if not verify_roundtrip(pieces, original):
            errors.append(f"{chunk['chunk_id']}: склейка пунктов не совпала со статьёй")
            continue

        head = _ARTICLE_HEAD.match(original)
        article_label = f"статья {head.group(1)}" if head else (
            chunk["location"].get("article") or ""
        )
        article_title = head.group(2).strip() if head else ""

        for index, (point, piece) in enumerate(pieces):
            # Преамбула = заголовок статьи, это не норма. В чанк не идёт,
            # но в целостности уже учтена (verify_roundtrip выше).
            if point is None:
                continue
            suffix = f"п-{point.replace('.', '-')}"
            label = f"{article_label}, пункт {point}"
            new_chunks.append(
                {
                    "chunk_id": f"{chunk['chunk_id']}-{suffix}",
                    "title": f"{article_label.capitalize()}. {article_title} — пункт {point}",
                    "location": location_for(article_label, point),
                    "text": piece,
                    # «восстановлен»: текст дословный, склейка проверена скриптом,
                    # но глазами вторым человеком ещё не сверен (шаг 4 конвейера).
                    "status": "восстановлен",
                    "_label": label,
                }
            )

    for item in new_chunks:
        item.pop("_label", None)
    doc["chunks"] = new_chunks
    return doc, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="записать результат в файл")
    args = parser.parse_args()

    for path in sorted(CORPUS.glob("*.json")):
        doc, errors = rechunk_doc(path)
        sizes = [len(c["text"]) for c in doc["chunks"]]
        print(f"{path.name}")
        print(f"  чанков: {len(doc['chunks'])}  (было статей: {len(json.loads(path.read_text(encoding='utf-8'))['chunks'])})")
        print(f"  размер: min {min(sizes)} / медиана {sorted(sizes)[len(sizes)//2]} / max {max(sizes)} символов")
        if errors:
            print("  ОШИБКИ:")
            for error in errors:
                print(f"    {error}")
        else:
            print("  целостность: склейка пунктов == исходная статья (ok)")
        if args.write and not errors:
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  записано: {path.name}")


if __name__ == "__main__":
    main()
