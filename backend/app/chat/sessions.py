"""Память диалога: контекст последних реплик по session_id.

Зачем: без истории вопрос «а какие документы на второе?» теряет смысл —
непонятно, какое «второе». С историей оркестратор подмешивает предыдущий
разговор в промпт и в поисковый запрос.

MVP-ограничения (осознанно):
- в памяти процесса (перезапуск API = история сброшена). Для пилота — Redis,
  интерфейс класса не меняется;
- ПДн не храним: только текст диалога, ключ — анонимный session_id с фронта.
"""

import time
from collections import OrderedDict


class SessionMemory:
    """Последние N реплик диалога по session_id. Thread-safe."""

    def __init__(self, max_turns: int = 6, max_sessions: int = 500, ttl_hours: float = 24) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_hours * 3600
        # OrderedDict для LRU-вытеснения самых старых сессий
        self._sessions: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()

    def history(self, session_id: str | None) -> list[dict]:
        """Реплики диалога в порядке следования: [{role, content}, ...]."""
        entry = self._sessions.get(session_id or "")
        if entry is None:
            return []
        stored_at, turns = entry
        if time.time() - stored_at > self.ttl_seconds:
            del self._sessions[session_id or ""]
            return []
        self._sessions.move_to_end(session_id or "")
        return list(turns)

    def append(self, session_id: str | None, role: str, content: str) -> None:
        if not session_id or not content:
            return
        _stored_at, turns = self._sessions.get(session_id, (time.time(), []))
        turns.append({"role": role, "content": content})
        # Держим чётное число реплик (пары вопрос-ответ), чтобы не склеить
        # два вопроса подряд без ответов между ними.
        while len(turns) > self.max_turns:
            turns.pop(0)
        self._sessions[session_id] = (time.time(), turns)
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
