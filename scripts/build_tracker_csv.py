#!/usr/bin/env python3
"""Собирает CSV трекера актов из проверенной таблицы в docs/.

Источник истины — docs/исследования/акты-пособия-трекер.md: таблица актов,
где каждая ссылка открыта с браузерным UA (HTTP 200 + название в тексте).
Скрипт только переводит markdown-таблицу в CSV для импорта в Google Sheets —
новых реквизитов не добавляет и ничего не додумывает.

Импорт: Google Sheets → Файл → Импорт → CSV, разделитель «запятая», UTF-8.
Запуск: python3 scripts/build_tracker_csv.py
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "docs" / "исследования" / "акты-пособия-трекер.md"
OUT = REPO / "kb_corpus" / "трекер.csv"

HEADER = [
    "No", "Акт (название)", "Вид", "Номер", "Дата", "Редакция",
    "Ссылка pravo.by", "Владелец", "Статус", "Комментарий",
]


def _clean(cell: str) -> str:
    """Убирает markdown-разметку: ссылки → url, жирный/тильды → текст."""
    cell = cell.strip()
    cell = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\2", cell)
    cell = cell.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", cell).strip()


def parse_table(markdown: str) -> list[list[str]]:
    """Строки markdown-таблицы с 9 колонками (No…Комментарий)."""
    rows: list[list[str]] = []
    header_seen = False
    for line in markdown.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = line.strip().strip("|").split("|")
        if len(cells) < 9:
            continue
        if not header_seen:
            header_seen = True
            continue
        if set(cells[0].strip()) <= {"-", ":", " "}:
            continue
        rows.append([_clean(cell) for cell in cells[:9]])
    return rows


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"нет источника: {SOURCE.relative_to(REPO)}")

    rows = parse_table(SOURCE.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit("в источнике не нашлось таблицы актов")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for no, title, kind, number, date, edition, url, status, comment in rows:
            writer.writerow(
                [no, title, kind, number, date, edition, url, "@jaraslaukunin", status, comment]
            )

    in_corpus = sum(1 for row in rows if row[7] in {"разобран", "скачан"})
    print(f"записано: {OUT.relative_to(REPO)} — {len(rows)} строк (в корпусе: {in_corpus})")
    print("импорт: Google Sheets → Файл → Импорт → CSV, UTF-8")


if __name__ == "__main__":
    main()
