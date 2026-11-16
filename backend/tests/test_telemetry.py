from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from app.services import telemetry


def test_active_request_stages_and_persisted_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "get_settings", lambda: SimpleNamespace(api_keys_db=tmp_path / "keys.db"))
    entered, release = Event(), Event()

    @telemetry.measured
    def answer():
        telemetry.stage("generation")
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(source="corpus")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(answer)
        assert entered.wait(3)
        try:
            running = telemetry.snapshot(1)
            assert len(running["active"]) == 1
            assert running["active"][0]["stage"] == "generation"
        finally:
            release.set()
        future.result()

    @telemetry.measured
    def failing():
        raise ValueError("private input must not be persisted")

    with pytest.raises(ValueError):
        failing()
    report = telemetry.snapshot(1)
    assert report["total"] == 2
    assert report["sources"] == {"corpus": 1, "error": 1}
    assert report["active"] == []
    assert sum(bucket["count"] for bucket in report["buckets"]) == 2
    assert report["p95"] >= report["median"] > 0
    assert b"private input" not in (tmp_path / "telemetry.sqlite3").read_bytes()
