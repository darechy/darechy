"""Валидатор корпуса: `python -m app.kb.validate [kb_corpus/curated]`.

Проверяет то, что конвейер обещает перед индексацией:
- файл читается и соответствует `CuratedDoc` (схема не разъехалась);
- у акта есть реквизиты: вид, номер, дата, название, ссылка на pravo.by;
- chunk_id уникальны и непустые;
- текст чанка не пустой и не обрубок;
- location (статья/пункт) заполнен — иначе цитата «повиснет» без места;
- статус из известного набора: проверен / восстановлен / разобран.

Запуск из backend/:      python -m app.kb.validate
Для другой папки:        python -m app.kb.validate ../kb_corpus/curated
Код возврата 1, если есть ошибки — можно ставить в CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core.config import REPO_DIR, get_settings
from app.kb.models import CuratedDoc

KNOWN_STATUSES = {"проверен", "восстановлен", "разобран"}
MIN_CHUNK_CHARS = 10
# Слишком крупный чанк — признак того, что разборщик не понял структуру акта:
# один «пункт» всосал полдокумента. Такой чанк и в промпт не влезет, и в
# поиске будет вытеснять точные фрагменты. Лучше поймать это валидатором.
MAX_CHUNK_CHARS = 15000


def validate_doc(path: Path) -> list[str]:
    """Возвращает список ошибок по одному файлу корпуса."""
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"битый JSON: {exc}"]

    try:
        doc = CuratedDoc.model_validate(payload)
    except ValueError as exc:
        return [f"схема не приняла файл: {exc}"]

    for field in ("type", "number", "date", "title", "source_url"):
        if not getattr(doc.doc, field, ""):
            errors.append(f"у акта не заполнено поле «{field}»")
    if "pravo.by" not in (doc.doc.source_url or ""):
        errors.append("source_url не похож на официальный источник (pravo.by)")

    seen: set[str] = set()
    for chunk in doc.chunks:
        if not chunk.chunk_id:
            errors.append("чанк без chunk_id")
        elif chunk.chunk_id in seen:
            errors.append(f"дубль chunk_id: {chunk.chunk_id}")
        seen.add(chunk.chunk_id)

        if not chunk.location.article and not chunk.location.point:
            errors.append(f"{chunk.chunk_id}: не заполнено location (статья/пункт)")
        if len(chunk.text.strip()) < MIN_CHUNK_CHARS:
            errors.append(f"{chunk.chunk_id}: текст короче {MIN_CHUNK_CHARS} символов")
        if len(chunk.text) > MAX_CHUNK_CHARS:
            errors.append(
                f"{chunk.chunk_id}: чанк {len(chunk.text)} знаков — больше "
                f"{MAX_CHUNK_CHARS}, разбор не понял структуру акта"
            )
        if chunk.status not in KNOWN_STATUSES:
            errors.append(f"{chunk.chunk_id}: неизвестный статус «{chunk.status}»")

    return errors


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else get_settings().corpus_dir
    if not target.exists():
        print(f"нет папки: {target}")
        sys.exit(1)

    files = sorted(target.glob("*.json"))
    if not files:
        print(f"в {target} нет ни одного .json — корпус пуст")
        sys.exit(1)

    total_errors = 0
    total_chunks = 0
    for path in files:
        errors = validate_doc(path)
        try:
            chunks = len(json.loads(path.read_text(encoding="utf-8")).get("chunks", []))
        except json.JSONDecodeError:
            chunks = 0
        total_chunks += chunks
        if errors:
            total_errors += len(errors)
            print(f"✗ {path.name}")
            for error in errors[:10]:
                print(f"    {error}")
            if len(errors) > 10:
                print(f"    … и ещё {len(errors) - 10}")
        else:
            print(f"✓ {path.name} — {chunks} чанков")

    print()
    print(f"актов: {len(files)}, чанков: {total_chunks}, ошибок: {total_errors}")
    if total_errors:
        print("Валидация не пройдена — в индекс такое пускать нельзя.")
        sys.exit(1)
    print(f"Валидация пройдена. Папка: {target.relative_to(REPO_DIR) if REPO_DIR in target.parents else target}")


if __name__ == "__main__":
    main()