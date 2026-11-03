"""Регресс-прогон golden set через живой API — «прибор качества» RAG.

Запуск из корня репо (API должен работать на localhost:8000):
    python3 eval/run_eval.py                 # eval/golden_set.json
    python3 eval/run_eval.py eval/my.json    # другой файл

Проверяет: факт-вопросы не отказывают и цитируются; отказные — отказывают;
навигационные — подсказывают ожидаемые услуги. Итог + история в eval/history/.
Цели к демо: ≥90% факт-ответов с цитатами, 100% честных отказов.
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
API_URL = "http://localhost:8000/api/chat"
HISTORY_DIR = Path(__file__).resolve().parent / "history"


def call_chat(message: str, timeout: float = 60.0) -> dict:
    body = json.dumps({"message": message, "session_id": f"eval-{int(time.time())}"}).encode()
    req = urllib.request.Request(
        API_URL, data=body, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    result["wall_s"] = round(time.perf_counter() - started, 2)
    return result


def judge(case: dict, response: dict) -> tuple[bool, str]:
    """(прошёл?, пояснение). Логика по типу кейса."""
    citations_text = " ".join(
        f"{c.get('doc_title', '')} {c.get('location', '')}" for c in response.get("citations", [])
    )
    if case.get("expected_refusal"):
        if response.get("refusal"):
            return True, "отказ как ожидалось"
        return False, "должен был отказаться, но ответил"
    if response.get("refusal"):
        return False, "ложный отказ"
    # Проверка содержимого ответа: без неё «заявление» на вопрос «что оформлять»
    # проходил как успех, хотя пользы в нём нет.
    expected_answer = case.get("expected_answer_contains") or []
    answer_lower = response.get("answer", "").lower()
    for needle in expected_answer:
        if needle.lower() not in answer_lower:
            return False, f"в ответе нет «{needle}»"

    expected_citation = case.get("expected_citation_contains")
    if expected_citation and expected_citation not in citations_text:
        return False, f"цитата не содержит «{expected_citation}»"
    expected_services = case.get("expected_services") or []
    if expected_services:
        suggested = set(response.get("suggested_services", []))
        if not set(expected_services) & suggested:
            return False, f"среди подсказок нет {expected_services}"
    return True, "ok"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "golden_set.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    passed = 0
    rows: list[dict] = []
    for case in cases:
        try:
            response = call_chat(case["question"])
            ok, note = judge(case, response)
        except Exception as exc:  # noqa: BLE001 — сетевые ошибки тоже кейс-результат
            ok, note = False, f"API недоступен: {exc}"
            response = {}
        passed += ok
        rows.append({**case, "ok": ok, "note": note, "response": response})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {case['id']}: {note}")
        if not ok and response.get("answer"):
            print(f"       ответ: {response['answer'][:120]}…")

    total = len(cases)
    print(f"\nИтог: {passed}/{total} ({100 * passed / total:.0f}%)")
    HISTORY_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = HISTORY_DIR / f"{stamp}_{path.stem}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"История: {out.relative_to(REPO_DIR)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
