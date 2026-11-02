"""Каталог услуг и жизненных ситуаций.

Неделя 1: каталог из стаба JSON (`backend/app/data/services_stub.json`).
Неделя 2: переезд в Postgres — контракт моделей не меняется, меняется только
эта прослойка.
"""

import json
import threading
from pathlib import Path

from app.core.config import BACKEND_DIR, Settings
from app.models.schemas import Service, Situation

# Названия жизненных ситуаций на главной портала (человеческим языком).
SITUATION_TITLES: dict[str, str] = {
    "rozhdenie-rebenka": "Родился ребёнок",
}


class Catalog:
    """Короткоживущий кэш каталога услуг; перечитывается по mtime файла."""

    def __init__(self, services_file: Path) -> None:
        self._services_file = services_file
        self._lock = threading.Lock()
        self._mtime: float = -1.0
        self._services: list[Service] = []

    def _load(self) -> None:
        mtime = self._services_file.stat().st_mtime
        with self._lock:
            if mtime != self._mtime:
                raw = json.loads(self._services_file.read_text(encoding="utf-8"))
                self._services = [Service.model_validate(item) for item in raw]
                self._mtime = mtime

    def all(self) -> list[Service]:
        self._load()
        return list(self._services)

    def get(self, service_id: str) -> Service | None:
        return next((s for s in self.all() if s.id == service_id), None)

    def situations(self) -> list[Situation]:
        by_situation: dict[str, list[str]] = {}
        for service in self.all():
            by_situation.setdefault(service.situation, []).append(service.id)
        return [
            Situation(
                id=sid,
                title=SITUATION_TITLES.get(sid, sid),
                service_ids=service_ids,
            )
            for sid, service_ids in by_situation.items()
        ]


def build_catalog(settings: Settings) -> Catalog:
    """Фабрика каталога. Читает файл, который правит админка (`services.json`).

    Стаб `services_stub.json` остаётся запасным вариантом, но источник истины
    теперь редактируемый — «добавить услугу» больше не значит «правь код».
    """
    if settings.services_file.exists():
        return Catalog(settings.services_file)
    return Catalog(BACKEND_DIR / "app" / "data" / "services_stub.json")
