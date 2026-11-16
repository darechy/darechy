"""Audit actual Ollama generation and BGE separately from application routing.

Run with backend/.venv/bin/python eval/audit_models.py --models qwen3:1.7b
Use --models with several installed model names for a sequential comparison.
Results include full answers for manual review, never an invented quality score.
"""
import argparse
import json
import math
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import httpx
from app.core.config import Settings


def call(base, path, payload=None):
    with httpx.Client(timeout=120) as client:
        response = client.get(base + path) if payload is None else client.post(base + path, json=payload)
        response.raise_for_status()
        return response.json()


def cosine(a, b):
    return sum(x*y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))


def main():
    settings = Settings(_env_file=ROOT / 'backend/.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', default=[settings.llm_model])
    args = parser.parse_args()
    report = {'created': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'configured_model': settings.llm_model, 'models': {}, 'errors': []}
    output = ROOT / 'eval/history' / (datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '_model_audit.json')
    output.parent.mkdir(exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    base = settings.ollama_base_url.rstrip('/')
    try:
        report['ollama_version'] = call(base, '/api/version')
        report['installed'] = call(base, '/api/tags')
        report['resident_before'] = call(base, '/api/ps')
        report['app_health'] = call('http://127.0.0.1:8000', '/health/models')
    except Exception as error:
        report['errors'].append(str(error))
        save()
        print(f'Live services unavailable: {error}\nReport: {output}')
        return 2

    # A smoke test, not corpus recall or a representative accuracy estimate.
    pairs = [
        ('Сижу с малышом дома, ему полтора года', 'Пособие по уходу за ребёнком до трёх лет'),
        ('У нас появился новорождённый', 'Единовременная выплата при рождении ребёнка'),
        ('Кошка умерла, куда обратиться?', 'Ветеринарная служба и кремация домашних животных'),
        ('Наниматель меня сократил', 'Регистрация безработного и помощь в поиске работы'),
        ('Хочу поговорить, чувствую себя одиноким', 'Эмоциональная поддержка и общение'),
        ('Потерял удостоверение личности', 'Восстановление утраченного паспорта'),
    ]
    try:
        start = time.perf_counter()
        embedded = call(base, '/api/embed', {'model': settings.embedding_model,
            'input': [q for q, _ in pairs] + [d for _, d in pairs], 'keep_alive': settings.embed_keep_alive})
        vectors = embedded['embeddings']
        assert len(vectors) == len(pairs) * 2
        assert all(len(v) == settings.embedding_dim and all(math.isfinite(x) for x in v) and any(v) for v in vectors)
        matches = []
        for index, (question, _) in enumerate(pairs):
            scores = [cosine(vectors[index], v) for v in vectors[len(pairs):]]
            predicted = max(range(len(scores)), key=scores.__getitem__)
            matches.append({'query': question, 'matched': pairs[predicted][1], 'correct': predicted == index, 'scores': scores})
        report['embedding_smoke'] = {'seconds': time.perf_counter()-start, 'dimension': settings.embedding_dim,
                                     'load_s': embedded.get('load_duration', 0)/1e9, 'cases': matches}
        print('BGE semantic smoke:', sum(row['correct'] for row in matches), '/', len(pairs), flush=True)
    except Exception as error:
        report['errors'].append('Embedding: ' + str(error))
    save()

    tasks = []
    for amount, deadline in [(73, 12), (146, 25), (219, 41), (308, 56)]:
        context = f'Учебная вымышленная услуга. Выплата {amount} рублей. Срок подачи {deadline} дней. Размер дохода не указан.'
        tasks.extend([
            {'prompt': context + ' Сколько рублей выплачивают? Ответь только числом.', 'expected': str(amount)},
            {'prompt': context + ' Каков срок подачи? Ответь только числом.', 'expected': str(deadline)},
            {'prompt': context + ' Какой доход допустим? Ответь НЕТ ДАННЫХ, если ответа нет.', 'expected': 'НЕТ ДАННЫХ'},
        ])
    tasks.extend({'prompt': text, 'expected': None} for text in [
        'У меня умерла кошка. Мне тяжело, что делать?',
        'Я потерял маму. Мне одиноко и хочется поговорить.',
        'Меня сократили. Я растерян. С чего начать?',
        'Я сказал, что потерял маму. Ты спросил, с кем я могу поговорить. Никого рядом нет. Что теперь?',
    ])
    for model in args.models:
        rows = []
        report['models'][model] = rows
        for index, task in enumerate(tasks):
            start = time.perf_counter()
            row = {'case': index+1, **task}
            try:
                result = call(base, '/api/chat', {'model': model, 'stream': False, 'think': False,
                    'keep_alive': settings.chat_keep_alive, 'options': {'temperature': 0, 'num_predict': 180},
                    'messages': [{'role': 'system', 'content': 'Отвечай по-русски, кратко. Следуй инструкции. При личном горе проявляй сочувствие. Не выдумывай факты, контакты и результаты интернет-поиска.'},
                                 {'role': 'user', 'content': task['prompt']}]})
                raw_message = result['message']
                answer = raw_message['content'].strip()
                finished = result.get('done_reason') != 'length'
                row.update(answer=answer, thinking=raw_message.get('thinking'), finished=finished,
                           seconds=time.perf_counter()-start,
                           load_s=result.get('load_duration', 0)/1e9,
                           prompt_s=result.get('prompt_eval_duration', 0)/1e9,
                           generate_s=result.get('eval_duration', 0)/1e9,
                           tokens=result.get('eval_count'), done_reason=result.get('done_reason'),
                           passed=finished and answer.rstrip('.') == task['expected'] if task['expected'] else None)
            except Exception as error:
                row.update(error=str(error), seconds=time.perf_counter()-start, passed=False if task['expected'] else None)
            rows.append(row)
            save()
            print(model, index+1, row.get('passed'), round(row['seconds'], 2), row.get('answer', row.get('error')), flush=True)
        checked = [row for row in rows if row['expected'] is not None]
        print(model, 'synthetic exact checks:', sum(row['passed'] for row in checked), '/', len(checked),
              'median seconds:', round(statistics.median(row['seconds'] for row in rows), 2),
              'Support answers require manual review.', flush=True)
    report['resident_after'] = call(base, '/api/ps')
    save()
    print('Report:', output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
