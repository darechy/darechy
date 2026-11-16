"""Память диалога: контекст последних реплик по session_id.

Зачем: без истории вопрос «а какие документы на второе?» теряет смысл —
непонятно, какое «второе». С историей оркестратор подмешивает предыдущий
разговор в промпт и в поисковый запрос.

MVP-ограничения (осознанно):
- в памяти процесса (перезапуск API = история сброшена). Для пилота — Redis,
  интерфейс класса не меняется;
- ПДн не храним: только текст диалога, ключ — анонимный session_id с фронта.
"""

import threading
import time
from collections import OrderedDict


class SessionMemory:
    """Последние N реплик диалога по session_id. Thread-safe."""

    def __init__(self, max_turns: int = 6, max_sessions: int = 500, ttl_hours: float = 24) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_hours * 3600
        # OrderedDict для LRU-вытеснения самых старых сессий
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()

    def history(self, session_id: str | None) -> list[dict]:
        """Реплики диалога в порядке следования: [{role, content}, ...]."""
        if not session_id:
            return []
        with self._lock:
            turns = self._active_turns(session_id, time.monotonic())
            if turns:
                self._sessions.move_to_end(session_id)
            return [turn.copy() for turn in turns]

    def _active_turns(self, session_id: str, now: float) -> list[dict]:
        stored_at, turns = self._sessions.get(session_id, (now, []))
        if now - stored_at > self.ttl_seconds:
            del self._sessions[session_id]
            return []
        return turns

    def append(self, session_id: str | None, role: str, content: str) -> None:
        if content:
            self._append(session_id, [{"role": role, "content": content}])

    def append_exchange(self, session_id: str | None, user: str, assistant: str | None) -> None:
        turns = [{"role": "user", "content": user}]
        if assistant:
            turns.append({"role": "assistant", "content": assistant})
        self._append(session_id, turns)

    def _append(self, session_id: str | None, turns: list[dict]) -> None:
        if not session_id:
            return
        with self._lock:
            now = time.monotonic()
            recent = self._active_turns(session_id, now) + turns
            self._sessions[session_id] = (now, recent[-self.max_turns:])
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
