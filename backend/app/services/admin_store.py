"""Редактируемый каталог услуг: админка пишет в тот же файл, что читает портал.

Каталог портала (`app/services/catalog.py`) читает `app/data/services.json`
и перечитывает его по mtime. Поэтому админке достаточно атомарно записать
файл — изменения подхватятся на следующем запросе, без перезапуска.

Здесь же «логика госуслуг»: услуга описывается не кодом, а данными —
кто имеет право, размер, срок, чек-лист документов, источники. Админка даёт
это править, а `CHECKLIST`/`SOURCE` собираются подстановкой чанков корпуса,
чтобы цитата на странице услуги бралась из акта, а не набиралась руками.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

from app.kb.models import Chunk
from app.models.schemas import Service

logger = logging.getLogger(__name__)

# Поля, которые заполняются из корпуса: админка показывает их как «из акта».
_DERIVED_FIELDS = ("amount", "deadline", "who")


def slug(text: str) -> str:
    """«Пособие по уходу за ребёнком до 3 лет» → «posobie-po-uhodu-za-rebenkom-do-3-let»."""
    table = str.maketrans(
        {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
            "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
            "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
            "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        }
    )
    lowered = text.lower().translate(table)
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return lowered or "service"


class ServiceCatalogStore:
    """CRUD услуг поверх JSON. Потокобезопасно, запись атомарная."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    # --- чтение -------------------------------------------------------------

    def all(self) -> list[Service]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Каталог услуг не прочитан (%s): %s", self.path, exc)
            return []
        return [Service.model_validate(item) for item in raw]

    def get(self, service_id: str) -> Service | None:
        return next((s for s in self.all() if s.id == service_id), None)

    # --- запись -------------------------------------------------------------

    def _save(self, services: list[Service]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [s.model_dump(mode="json") for s in services]
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, service: Service, *, new_id_if_taken: bool = False) -> Service:
        """Создаёт или обновляет услугу по id."""
        with self._lock:
            services = self.all()
            for index, existing in enumerate(services):
                if existing.id == service.id:
                    services[index] = service
                    self._save(services)
                    return service
            services.append(service)
            self._save(services)
            return service

    def create_from_title(self, title: str, situation: str) -> Service:
        """Пустая заготовка услуги: админка даёт её заполнить и выбрать чанки."""
        base = slug(title)
        existing_ids = {s.id for s in self.all()}
        service_id = base
        counter = 2
        while service_id in existing_ids:
            service_id = f"{base}-{counter}"
            counter += 1

        service = Service(
            id=service_id,
            title=title,
            situation=situation,
            summary="",
            who="",
            amount="",
            deadline="",
            checklist=[],
            sources=[],
        )
        return self.upsert(service)

    def delete(self, service_id: str) -> bool:
        with self._lock:
            services = self.all()
            remaining = [s for s in services if s.id != service_id]
            if len(remaining) == len(services):
                return False
            self._save(remaining)
            return True

    def set_situation_title(self, situation_id: str, title: str) -> None:
        self.upsert_situation(situation_id, title=title)

    def upsert_situation(
        self,
        situation_id: str,
        title: str | None = None,
        service_ids: list[str] | None = None,
        note: str | None = None,
    ) -> dict:
        """Правит жизненную ситуацию: название, состав услуг, пояснение.

        Явный список услуг делает ситуацию **сквозной**: одна ситуация собирает
        услуги из разных актов (регистрация рождения в ЗАГСе + пособия).
        """
        path = self.path.with_name("situations.json")
        payload: dict = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}

        current = payload.get(situation_id)
        entry = dict(current) if isinstance(current, dict) else (
            {"title": current} if isinstance(current, str) else {}
        )
        if title is not None:
            entry["title"] = title
        if service_ids is not None:
            entry["service_ids"] = service_ids
        if note is not None:
            entry["note"] = note
        payload[situation_id] = entry
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry

    def delete_situation(self, situation_id: str) -> bool:
        path = self.path.with_name("situations.json")
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if situation_id not in payload:
            return False
        payload.pop(situation_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def situation_titles(self) -> dict[str, str]:
        path = self.path.with_name("situations.json")
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def chunk_options(chunks: list[Chunk], query: str = "", limit: int = 50) -> list[dict]:
    """Список чанков для выбора источника на странице услуги.

    Админка не должна требовать помнить `chunk_id` наизусть — это и есть
    «из кода это делать — хуйня полная».
    """
    lowered = query.lower().strip()
    items: list[dict] = []
    for chunk in chunks:
        haystack = f"{chunk.title or ''} {chunk.location.label()} {chunk.text}".lower()
        if lowered and lowered not in haystack:
            continue
        items.append(
            {
                "chunk_id": chunk.chunk_id,
                "title": chunk.title or chunk.location.label(),
                "doc": f"{chunk.doc_title or ''} ({chunk.doc_number or ''})".strip(),
                "preview": chunk.text[:220],
                "status": chunk.status,
            }
        )
    return items[:limit]
