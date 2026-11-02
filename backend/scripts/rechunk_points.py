"""Разбор статей закона на чанки-пункты (разовый прогон из терминала).

Требование `kb_corpus/README.md`: чанк = пункт, а не статья. Статья целиком
даёт 12 400 символов мусора в промпте и неточную цитату.

Логика разбора живёт в `app/kb/corpus.py` — тот же код использует админка
(`POST /api/admin/corpus/import`): две копии разборщика неизбежно разъедутся,
а цена этому — потерянный при нарезке текст акта.

Гарантия честности: текст не переписывается, склейка пунктов обязана совпасть
с исходной статьёй (`verify_roundtrip`); не совпала — чанк не создаётся.

Запуск из backend/:
    python -m scripts.rechunk_points           # только отчёт
    python -m scripts.rechunk_points --write    # записать в curated/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.kb.corpus import chunk_article  # noqa: E402
from app.kb.models import Chunk  # noqa: E402


def rechunk_doc(path: Path, status: str) -> tuple[dict, list[str]]:
    """Пересобирает curated-файл: статья → пункты, с проверкой целостности."""
    original = json.loads(path.read_text(encoding="utf-8"))
    chunks: list[Chunk] = []
    errors: list[str] = []

    for chunk in original["chunks"]:
        article_hint = chunk.get("location", {}).get("article")
        report = chunk_article(
            chunk["text"],
            doc_prefix=chunk["chunk_id"].rsplit("_st-", 1)[0],
            article_hint=article_hint,
            status=status,
        )
        if report.errors:
            errors.extend(f"{chunk['chunk_id']}: {error}" for error in report.errors)
            continue
        chunks.extend(report.chunks)

    payload = {"doc": original["doc"], "chunks": [c.model_dump(mode="json") for c in chunks]}
    return payload, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="записать результат в файл")
    parser.add_argument("--status", default="восстановлен", help="статус новых чанков")
    args = parser.parse_args()

    for path in sorted(get_settings().corpus_dir.glob("*.json")):
        before = len(json.loads(path.read_text(encoding="utf-8"))["chunks"])
        payload, errors = rechunk_doc(path, args.status)
        sizes = [len(c["text"]) for c in payload["chunks"]]
        print(f"{path.name}")
        print(f"  чанков: {len(payload['chunks'])} (было статей: {before})")
        if sizes:
            print(
                f"  размер: min {min(sizes)} / "
                f"медиана {sorted(sizes)[len(sizes) // 2]} / max {max(sizes)}"
            )
        if errors:
            print("  ОШИБКИ:")
            for error in errors:
                print(f"    {error}")
            continue
        print("  целостность: склейка пунктов == исходная статья (ok)")
        if args.write:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  записано: {path.name}")


if __name__ == "__main__":
    main()