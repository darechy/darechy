"""Регресс-прогон golden set через живой API — «прибор качества» RAG.

Запуск из корня репо (API должен работать на localhost:8000):
    python3 eval/run_eval.py                 # eval/golden_set.json
    python3 eval/run_eval.py eval/my.json    # другой файл

Проверяет: факт-вопросы не отказывают и цитируются; отказные — отказывают;
навигационные — подсказывают ожидаемые услуги. Итог + история в eval/history/.
Цели к демо: ≥90% факт-ответов с цитатами, 100% честных отказов.
"""

import argparse
import json
import math
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_DIR = Path(__file__).resolve().parent.parent
API_URL = "http://localhost:8000/api/chat"
HISTORY_DIR = Path(__file__).resolve().parent / "history"


def call_chat(message: str, timeout: float = 150.0, api_url: str = API_URL) -> dict:
    body = json.dumps({"message": message, "session_id": f"eval-{uuid4().hex}"}).encode()
    req = urllib.request.Request(
        api_url, data=body, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    result["wall_s"] = round(time.perf_counter() - started, 2)
    return result


def judge(case: dict, response: dict) -> tuple[bool, str]:
    """(прошёл?, пояснение). Логика по типу кейса."""
    citations = response.get("citations", [])
    if case.get("expected_refusal"):
        if response.get("refusal"):
            return True, "отказ как ожидалось"
        return False, "должен был отказаться, но ответил"
    if response.get("refusal"):
        return False, "ложный отказ"
    if case.get("type") == "fact" and (
        not response.get("answer")
        or not any(
            citation.get("chunk_id") and citation.get("quote") and citation.get("location")
            for citation in citations
        )
    ):
        return False, "фактическому ответу нужна цитата с идентификатором и пунктом"
    # Проверка содержимого ответа: без неё «заявление» на вопрос «что оформлять»
    # проходил как успех, хотя пользы в нём нет.
    expected_answer = case.get("expected_answer_contains") or []
    answer_lower = response.get("answer", "").lower()
    for needle in expected_answer:
        if needle.lower() not in answer_lower:
            return False, f"в ответе нет «{needle}»"

    expected_citation = case.get("expected_citation_contains")
    expected_doc = case.get("expected_doc_number")
    if expected_citation and not any(
        re.search(rf"(?<!\w){re.escape(expected_citation)}(?![\w.-])",
                  f"{citation.get('doc_title') or ''} {citation.get('location') or ''}",
                  re.IGNORECASE)
        and (not expected_doc or expected_doc == citation.get("doc_number"))
        for citation in citations
    ):
        return False, f"цитата не содержит «{expected_citation}»"
    expected_services = case.get("expected_services") or []
    if expected_services:
        suggested = set(response.get("suggested_services", []))
        if not set(expected_services) & suggested:
            return False, f"среди подсказок нет {expected_services}"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", default=Path(__file__).parent / "golden_set.json")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--timeout", type=float, default=150.0)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))["cases"]
    if not cases:
        parser.error("Набор вопросов пуст")
    passed = Counter()
    totals = Counter(case.get("type", "other") for case in cases)
    sources = Counter()
    latencies: list[float] = []
    rows: list[dict] = []
    for case in cases:
        try:
            response = call_chat(case["question"], timeout=args.timeout, api_url=args.api_url)
            ok, note = judge(case, response)
        except Exception as exc:  # noqa: BLE001 — сетевые ошибки тоже кейс-результат
            ok, note = False, f"API недоступен: {exc}"
            response = {}
        passed[case.get("type", "other")] += ok
        sources[response.get("source", "unavailable")] += 1
        if "wall_s" in response:
            latencies.append(response["wall_s"])
        rows.append({**case, "ok": ok, "note": note, "response": response})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {case['id']}: {note}")
        if not ok and response.get("answer"):
            print(f"       ответ: {response['answer'][:120]}…")

    total = len(cases)
    all_passed = passed.total()
    print(f"\nИтог: {all_passed}/{total} ({100 * all_passed / total:.1f}%)")
    for kind, count in totals.items():
        print(f"  {kind}: {passed[kind]}/{count} ({100 * passed[kind] / count:.1f}%)")
    print("Источники ответов: " + ", ".join(f"{source}={count}" for source, count in sources.items()))
    if latencies:
        p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
        print(f"Время HTTP: медиана {statistics.median(latencies):.2f} с, p95 {p95:.2f} с")
    if not args.no_history:
        HISTORY_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out = HISTORY_DIR / f"{stamp}_{args.dataset.stem}.json"
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"История: {out.relative_to(REPO_DIR)}")
    return 0 if all_passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
