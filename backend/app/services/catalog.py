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
        """Жизненные ситуации. Состав и названия правятся файлом situations.json.

        Два режима, и оба нужны:
        - **по услугам** (по умолчанию): ситуация = группа услуг с тем же `situation`;
        - **сквозная ситуация**: в файле задан явный список `service_ids`, и тогда
          одна ситуация собирает услуги из разных актов и категорий. Пример:
          «Родился ребёнок» = регистрация рождения в ЗАГСе (Указ № 200) + три
          пособия (Закон № 7-З). Это и есть маршрут, который видит человек.

        Черновики в ситуации не попадают: их ещё не проверил человек.
        """
        explicit = self._situations_from_file()
        services = {s.id: s for s in self.all() if s.status != "черновик"}

        result: list[Situation] = []
        covered: set[str] = set()

        for sid, config in explicit.items():
            ids = [sid_ for sid_ in config.get("service_ids", []) if sid_ in services]
            if not ids:
                continue
            covered.update(ids)
            result.append(
                Situation(
                    id=sid,
                    title=config.get("title") or SITUATION_TITLES.get(sid, sid),
                    service_ids=ids,
                )
            )

        # Остальные услуги группируем по своей ситуации, чтобы ничего не пропало.
        by_situation: dict[str, list[str]] = {}
        for service in services.values():
            if service.id in covered:
                continue
            by_situation.setdefault(service.situation, []).append(service.id)
        for sid, ids in by_situation.items():
            title = explicit.get(sid, {}).get("title") or SITUATION_TITLES.get(sid, sid)
            result.append(Situation(id=sid, title=title, service_ids=ids))

        return result

    def _situations_from_file(self) -> dict[str, dict]:
        """Читает situations.json. Поддерживает и старый формат «id: заголовок»."""
        path = self._services_file.with_name("situations.json")
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                normalized[key] = {"title": value}
            elif isinstance(value, dict):
                normalized[key] = value
        return normalized


def build_catalog(settings: Settings) -> Catalog:
    """Фабрика каталога. Читает файл, который правит админка (`services.json`).

    Стаб `services_stub.json` остаётся запасным вариантом, но источник истины
    теперь редактируемый — «добавить услугу» больше не значит «правь код».
    """
    if settings.services_file.exists():
        return Catalog(settings.services_file)
    return Catalog(BACKEND_DIR / "app" / "data" / "services_stub.json")
