"""Хранилище черновиков карточек услуг и их утверждение.

Разделение принципиальное: черновики лежат ОТДЕЛЬНО от рабочего каталога
(`services.json`). Портал отдаёт только утверждённое, поэтому 480
сгенерированных карточек не могут случайно попасть на страницы.

Утверждение — перенос карточки в рабочий каталог со статусом «проверена»:
человек посмотрел и подписался. Отклонение — удаление черновика.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.models.schemas import Service

logger = logging.getLogger(__name__)


class DraftStore:
    """Черновики в JSON-файле. Потокобезопасно, запись атомарная."""

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
            logger.warning("Черновики не прочитаны (%s): %s", self.path, exc)
            return []
        return [Service.model_validate(item) for item in raw]

    def get(self, draft_id: str) -> Service | None:
        return next((d for d in self.all() if d.id == draft_id), None)

    def by_situation(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for draft in self.all():
            counts[draft.situation] = counts.get(draft.situation, 0) + 1
        return counts

    # --- запись -------------------------------------------------------------

    def _save(self, drafts: list[Service]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [draft.model_dump(mode="json") for draft in drafts]
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def replace_all(self, drafts: list[Service]) -> int:
        """Заменяет набор черновиков (импорт целиком). Возвращает число записей."""
        with self._lock:
            self._save(drafts)
        return len(drafts)

    def merge(self, drafts: list[Service], overwrite: bool = True) -> dict:
        """Добавляет черновики, не теряя уже отревьюенные пользователем.

        Черновик, который человек уже правил (`reviewed=True` в комментарии
        мы не храним — вместо этого статус), при повторном импорте не
        затирается: иначе правки терялись бы при каждом прогоне импортёра.
        """
        with self._lock:
            existing = {d.id: d for d in self.all()}
            added, updated, kept = 0, 0, 0
            for draft in drafts:
                current = existing.get(draft.id)
                if current is None:
                    existing[draft.id] = draft
                    added += 1
                elif overwrite and not current.who:
                    # Не затираем то, что человек уже дополнил (графа «кто имеет право»).
                    existing[draft.id] = draft
                    updated += 1
                else:
                    kept += 1
            self._save(list(existing.values()))
        return {"added": added, "updated": updated, "kept": kept, "total": len(existing)}

    def delete(self, draft_id: str) -> bool:
        with self._lock:
            drafts = self.all()
            remaining = [d for d in drafts if d.id != draft_id]
            if len(remaining) == len(drafts):
                return False
            self._save(remaining)
            return True

    def stats(self) -> dict:
        drafts = self.all()
        return {
            "total": len(drafts),
            "by_situation": self.by_situation(),
            "with_authority": sum(1 for d in drafts if d.authority),
            "with_documents": sum(1 for d in drafts if d.checklist),
            "filled_who": sum(1 for d in drafts if d.who),
        }
