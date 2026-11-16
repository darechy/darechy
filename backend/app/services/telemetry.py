"""Обезличенные замеры запросов. История в SQLite, активные этапы в памяти процесса."""

import math
import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from app.core.config import get_settings

_current = ContextVar("request_measurement", default=None)
_lock = threading.Lock()
_active = {}


@contextmanager
def _connect():
    path = get_settings().api_keys_db.parent / "telemetry.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3)
    try:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests (at REAL, duration REAL, source TEXT, routing REAL, retrieval REAL, generation REAL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS requests_at ON requests(at)")
            yield connection
    finally:
        connection.close()


def stage(name):
    key = _current.get()
    with _lock:
        if key in _active:
            row = _active[key]
            now = time.monotonic()
            row["timings"][row["stage"]] = (
                row["timings"].get(row["stage"], 0) + now - row["changed"]
            )
            row.update(stage=name, changed=now)


def measured(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        key = uuid4().hex[:12]
        started = time.monotonic()
        with _lock:
            _active[key] = dict(started=started, changed=started, stage="routing", timings={})
        token = _current.set(key)
        source = "error"
        try:
            response = function(*args, **kwargs)
            source = response.source
            return response
        finally:
            stage("done")
            with _lock:
                row = _active.pop(key)
            _current.reset(token)
            try:
                with _connect() as connection:
                    connection.execute(
                        "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            time.time(),
                            time.monotonic() - started,
                            source,
                            *(
                                row["timings"].get(name, 0)
                                for name in ("routing", "retrieval", "generation")
                            ),
                        ),
                    )
                    connection.execute(
                        "DELETE FROM requests WHERE at < ?", (time.time() - 30 * 86400,)
                    )
            except (OSError, sqlite3.Error):
                # Сбой статистики не прерывает ответ ассистента.
                pass

    return wrapped


def snapshot(hours):
    now = time.time()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT at,duration,source,routing,retrieval,generation FROM requests WHERE at >= ? ORDER BY at",
            (now - hours * 3600,),
        ).fetchall()
    durations = sorted(row[1] for row in rows)
    buckets = [dict(count=0, seconds=0) for _ in range(24)]
    sources = {}
    for at, duration, source, *_ in rows:
        index = min(23, max(0, int((at - (now - hours * 3600)) / (hours * 150))))
        buckets[index]["count"] += 1
        buckets[index]["seconds"] += duration
        sources[source] = sources.get(source, 0) + 1
    with _lock:
        active = [
            dict(id=key, stage=row["stage"], seconds=round(time.monotonic() - row["started"], 1))
            for key, row in _active.items()
        ]
    return dict(
        total=len(rows),
        median=statistics.median(durations) if rows else 0,
        p95=durations[math.ceil(len(rows) * 0.95) - 1] if rows else 0,
        sources=sources,
        buckets=buckets,
        active=active,
        stages={
            name: sum(row[index] for row in rows) / len(rows) if rows else 0
            for index, name in enumerate(("routing", "retrieval", "generation"), 3)
        },
    )
