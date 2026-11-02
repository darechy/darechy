"""Импорт акта из kb_corpus/raw в curated — из терминала, в три режима.

Режимы:
  articles   — акт со структурой «Статья N.» (законы): внутри каждой статьи
               режем на пункты, статья без пунктов = один чанк.
  flat       — акт без статей (постановления министерств): по нумерованным
               пунктам, иначе целиком.
  positions  — перечень административных процедур (Указ № 200): по позициям
               «2.5.», «2.35.» и т.д. Полный текст 1,9 МБ в индекс не пускаем —
               только пособийные позиции, иначе retrieval утонет.

Во всех режимах проверяется целостность: склеенный текст обязан совпасть
с исходником. Не совпал — файл не пишется.

Запуск из backend/:
    python -m scripts.import_act ../kb_corpus/raw/2001_55-Z_zakon-o-pogrebenii.md --mode articles
    python -m scripts.import_act ../kb_corpus/raw/2026_88_postanovlenie-bpm.md --mode flat
    python -m scripts.import_act ../kb_corpus/raw/2010_200_...md --mode positions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.kb.corpus import build_curated_doc, build_flat_act  # noqa: E402
from app.kb.models import CuratedDoc  # noqa: E402
from app.kb.positions import DEFAULT_POSITIONS, extract_positions  # noqa: E402


def _prefix(path: Path) -> str:
    return path.stem


def _write(doc: CuratedDoc, raw_path: Path, out_dir: Path) -> Path:
    target = out_dir / f"{_prefix(raw_path)}.json"
    payload = {
        "doc": doc.doc.model_dump(mode="json"),
        "chunks": [c.model_dump(mode="json") for c in doc.chunks],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_file", type=Path)
    parser.add_argument("--mode", choices=("articles", "flat", "positions"), default="articles")
    parser.add_argument("--status", default="восстановлен")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    raw = args.raw_file.read_text(encoding="utf-8")
    prefix = _prefix(args.raw_file)

    if args.mode == "positions":
        chunks, missing = extract_positions(raw, DEFAULT_POSITIONS, doc_prefix=prefix, status=args.status)
        if missing:
            print("⚠ не найдено:", "; ".join(missing))
        doc_meta, _title, _ = __import__("app.kb.corpus", fromlist=["parse_act_markdown"]).parse_act_markdown(raw)
        payload = {"doc": doc_meta, "chunks": [c.model_dump(mode="json") for c in chunks]}
        try:
            doc = CuratedDoc.model_validate(payload)
        except ValueError as exc:
            print(f"✗ схема не приняла документ: {exc}")
            sys.exit(1)
    elif args.mode == "flat":
        doc, errors = build_flat_act(raw, doc_prefix=prefix, status=args.status)
        if doc is None:
            print("✗", "; ".join(errors))
            sys.exit(1)
    else:
        doc, errors = build_curated_doc(raw, doc_prefix=prefix, status=args.status)
        if doc is None:
            print("✗", "; ".join(errors))
            sys.exit(1)

    sizes = [len(c.text) for c in doc.chunks]
    print(f"{args.raw_file.name} → режим {args.mode}")
    print(f"  акт: {doc.doc.number} от {doc.doc.date} «{doc.doc.title[:60]}»")
    print(f"  чанков: {len(doc.chunks)}; размеры: min {min(sizes)} / медиана {sorted(sizes)[len(sizes) // 2]} / max {max(sizes)}")

    if args.dry_run:
        print("  (dry-run: файл не пишется)")
        return

    target = _write(doc, args.raw_file, settings.corpus_dir)
    print(f"  записано: {target.name}")


if __name__ == "__main__":
    main()