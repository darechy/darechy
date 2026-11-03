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
# Названия жизненных ситуаций. Портал показывает их на главной; ситуации,
# пришедшие из разделов перечня (см. app/services/drafter.py), должны иметь
# человеческое имя, а не слаг. Правится файлом `app/data/situations.json`.
SITUATION_TITLES: dict[str, str] = {
    "rozhdenie-rebenka": "Родился ребёнок",
    "smert-blizkogo": "Умер близкий человек",
    "registraciya-aktov": "Регистрация актов гражданского состояния",
    "trud-i-soczaschita": "Труд и социальная защита",
    "socialnye-lgoty": "Документы на социальные льготы",
    "usynovlenie": "Усыновление, опека, попечительство",
    "zdravoohranenie": "Здравоохранение",
    "dokumenty-naseleniya": "Документы населения",
    "zhilyo": "Жилищные правоотношения",
    "stroitelstvo": "Архитектура и строительство",
    "kommunikacii": "Газо-, электро-, тепло- и водоснабжение, связь",
    "prirodopolzovanie": "Природопользование",
    "selskoe-hozyaystvo": "Сельское хозяйство",
    "voinskaya-obyazannost": "Воинская обязанность и служба",
    "oruzhie": "Оборот оружия",
    "nedvizhimost": "Регистрация недвижимости",
    "sport-i-kultura": "Физическая культура, спорт, культура",
    "ocenochnaya-deyatelnost": "Оценочная деятельность",
    "advokatura": "Адвокатская деятельность",
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
        """Жизненные ситуации из услуг + правки названий из situations.json.

        Названия редактируются данными: админка пишет файл, портал его читает.
        Так новая категория не требует правки кода.
        """
        titles = {**SITUATION_TITLES, **self._titles_from_file()}
        by_situation: dict[str, list[str]] = {}
        for service in self.all():
            # Черновики на портал не попадают — их ещё не проверил человек.
            if service.status == "черновик":
                continue
            by_situation.setdefault(service.situation, []).append(service.id)
        return [
            Situation(
                id=sid,
                title=titles.get(sid, sid),
                service_ids=service_ids,
            )
            for sid, service_ids in by_situation.items()
        ]

    def _titles_from_file(self) -> dict[str, str]:
        path = self._services_file.with_name("situations.json")
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def build_catalog(settings: Settings) -> Catalog:
    """Фабрика каталога. Читает файл, который правит админка (`services.json`).

    Стаб `services_stub.json` остаётся запасным вариантом, но источник истины
    теперь редактируемый — «добавить услугу» больше не значит «правь код».
    """
    if settings.services_file.exists():
        return Catalog(settings.services_file)
    return Catalog(BACKEND_DIR / "app" / "data" / "services_stub.json")
