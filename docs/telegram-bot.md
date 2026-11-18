# Telegram-бот «ДаРэчы»: сборка, запуск, проверка

Бот — тонкий клиент. Сам он ничего не «думает»: каждый вопрос он отправляет в тот же API, который обслуживает ассистента на сайте
(`POST /api/v1/chat`). Поэтому у бота та же модель, тот же поиск по нормативным актам, те же цитаты и та же
логика отказа «нет источника — нет ответа». Тело бота — `backend/app/bot/telegram.py` (aiogram 3, режим long polling).

```
Пользователь ─► Telegram ─► бот (python -m app.bot.telegram) ─► API /api/v1/* ─► RAG + модель через Ollama
                                        │
                                        ├─ голос → /api/v1/voice/transcribe (faster-whisper)
                                        └─ озвучка → /api/v1/voice/synthesize (Silero)
```

Что умеет бот: команда `/start`; текстовый вопрос (до 4096 символов); голосовое сообщение или аудиофайл (до 10 МБ) —
бот распознаёт речь, отвечает текстом с источниками и дополнительно озвучивает ответ (только по-русски). Длинные
ответы режутся на части по 2000 символов. Память диалога ведётся на стороне API по `session_id`; бот подставляет
не сырой id чата, а HMAC-хэш с случайным секретом, который меняется при каждом запуске (ПДн не храним).

> Важно: пока бот работает только пока запущен его процесс **и** доступен API. Выключили компьютер или Ollama —
> бот будет молчать или отвечать «База знаний сейчас недоступна». Чтобы бот жил круглосуточно, его
> нужно развернуть на сервере (шаг 9).

---

## 1. Что нужно поставить

| Инструмент | Зачем | macOS | Windows (PowerShell) |
|---|---|---|---|
| Python ≥ 3.11 | бэкенд и бот | `brew install python` | инсталлятор с python.org (отметьте *Add to PATH*) |
| Ollama | локальная модель и эмбеддинги | `brew install ollama` | инсталлятор с ollama.com |
| ffmpeg | голос (OGG/Opus из Telegram) | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| Docker Desktop | Qdrant (векторный поиск), необязательно | `brew install --cask docker` | инсталлятор с docker.com |
| Telegram | аккаунт для BotFather | — | — |

Без ffmpeg и без доп. пакетов из шага 5 бот работает, но только текстом. Qdrant тоже необязателен: без него поиск
идёт по BM25 (качество немного ниже).

## 2. Создать бота у BotFather

1. В Telegram откройте [@BotFather](https://t.me/BotFather) и отправьте `/newbot`.
2. Имя для отображения — например `ДаРэчы`. Имя пользователя должно оканчиваться на `bot` (например `darechy_bot`; если занято,
   выберите другое).
3. BotFather пришлёт **токен** вида `123456789:AA...`. Это пароль от бота: не коммитьте его и не пересылайте
   в чатах. Если токен утёк, отправьте BotFather `/revoke` и возьмите новый.
4. Оформление (по желанию), каждый пункт — отдельная команда BotFather с выбором вашего бота:
   - `/setdescription` — полное описание (до 512 символов), показывается на экране бота до первого сообщения.
   - `/setabouttext` — короткое описание (до 120 символов), показывается в профиле.
   - `/setuserpic` — аватар (логотип из `frontend/public`).
   - `/setcommands` — меню команд; сейчас бот знает только `start - Начать`.

## 3. Поднять модель и бэкенд

### 3.1. Модели Ollama

```bash
ollama serve                                   # если Ollama ещё не запущена (на Windows/macOS обычно уже фоном)
ollama pull qwen3:4b-instruct-2507-q4_K_M      # модель ответов (та же, что на сайте)
ollama pull bge-m3                             # эмбеддинги для поиска
```

Если позже вы смените модель в `backend/.env` (`LLM_MODEL`), бот получит её автоматически — в боте модель не прописана.

### 3.2. (Необязательно) Qdrant

```bash
docker compose up -d qdrant
curl http://localhost:6333/healthz
```

### 3.3. Виртуальное окружение и зависимости

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
```

Windows (PowerShell):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Если PowerShell ругается на запуск скриптов, выполните один раз `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 4. Ключи: админ-токен и API-ключ для бота

Бот ходит в `/api/v1/*`, а эти маршруты закрыты API-ключом. Ключ выпускается в админке.

1. Сгенерируйте админ-токен:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Впишите его в `backend/.env`: `ADMIN_TOKEN=<значение>`.
3. Запустите API (шаг 6.1) и откройте http://localhost:8000/api/admin. Введите админ-токен.
4. В разделе API-ключей создайте ключ с именем `telegram-bot`. **Значение показывается ровно один раз** — сразу скопируйте.
   Потеряли — отозвите ключ в той же админке и выпустите новый.
5. Отдельный ключ на каждый сервис позволяет отключить бота, не затронув остальных.

## 5. Настроить `backend/.env`

Минимум для бота:

```env
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_API_KEY=ключ_из_админки
TELEGRAM_API_BASE_URL=http://127.0.0.1:8000
ADMIN_TOKEN=ваш_админ_токен
```

- `TELEGRAM_API_BASE_URL` должен указывать на тот порт, на котором запущен API. При `uvicorn ... --port 8000` это `:8000`;
  если вы поднимаете всё через `scripts/dev.sh`, API стоит на `:8013` (порт задаётся переменной `DARECHY_API_PORT`).
- Бот читает `.env` из **текущей папки**, поэтому запускать его нужно из `backend/`.
- Файл `.env` в `.gitignore`. Токены в код и в историю git попасть не должны.

## 6. Запуск

Нужны два процесса в двух окнах терминала, оба из `backend/` с активным окружением.

### 6.1. API

```bash
uvicorn app.main:app --port 8000
```

Дождитесь строки в логе «Индекс корпуса: N чанков». Модель прогревается в фоне (`WARMUP_ON_START=true`).

### 6.2. Бот

```bash
python -m app.bot.telegram
```

Успешный старт — в логе нет ошибок, процесс не завершается. Если не заданы `TELEGRAM_BOT_TOKEN` или `TELEGRAM_API_KEY`, бот сразу скажет
об этом и выйдет.

## 7. Голос (необязательно)

```bash
pip install -r requirements-voice.txt
ffmpeg -version        # должно печатать версию; на Windows после winget откройте новое окно PowerShell
```

Перезапустите API после установки. При первом вызове скачиваются модели: распознавания (`WHISPER_MODEL`, по умолчанию
`large-v3-turbo`, около 1,5 ГБ) и синтеза Silero (около 100 МБ, нужен доступ к GitHub). Первое
голосовое сообщение поэтому обрабатывается дольше обычного. Распознавание идёт на CPU — на слабом
компьютере поставьте `WHISPER_MODEL=small` или `base` в `.env`.

Если голос не нужен, ничего делать не надо: бот просто попросит написать вопрос текстом.

## 8. Проверка по шагам

Идите сверху вниз — каждый шаг ловит свою проблему.

| # | Проверка | Ожидаемый результат |
|---|---|---|
| 1 | http://localhost:8000/health | `{"status": "ok", ...}` |
| 2 | http://localhost:8000/health/models | Ollama доступна, модели чата и эмбеддингов установлены |
| 3 | запрос с ключом (ниже) | JSON с `answer` и `citations` |
| 4 | в Telegram отправить боту `/start` | приветствие «Привет! Я «ДаРэчы»…» |
| 5 | написать вопрос текстом | ответ + блок «Источники» с цитатой |
| 6 | задать вопрос вне базы (например про погоду) | честный отказ без выдуманных источников |
| 7 | голосовое сообщение | «Распознаю запись…», ответ текстом и голосом |

Шаг 3, macOS / Linux:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer $TELEGRAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "Какие документы нужны для получения справки о месте жительства?"}'
```

Шаг 3, Windows (PowerShell):

```powershell
$key = 'ВАШ_КЛЮЧ'
$body = [Text.Encoding]::UTF8.GetBytes('{"message": "Какие документы нужны для получения справки о месте жительства?"}')
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/chat -Headers @{ Authorization = "Bearer $key" } -ContentType 'application/json; charset=utf-8' -Body $body
```

Первый ответ модели может идти до минуты (холодный старт модели на CPU), дальше быстрее. Таймаут бота на ответ API — 90 секунд.

Автотесты бота (без сети и без Telegram): `pytest -q tests/test_telegram.py` из `backend/`.

## 9. Работа на сервере без присмотра

Бот — обычный долгоживущий процесс, входящих портов открывать не нужно (long polling): нужен только исходящий
доступ к `api.telegram.org`. Для Linux-сервера создайте два systemd-сервиса (пути и пользователя подставьте свои).

`/etc/systemd/system/darechy-api.service`:

```ini
[Unit]
Description=DaRechy API
After=network-online.target

[Service]
WorkingDirectory=/opt/darechy/backend
ExecStart=/opt/darechy/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
User=darechy

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/darechy-bot.service`:

```ini
[Unit]
Description=DaRechy Telegram bot
After=darechy-api.service
Requires=darechy-api.service

[Service]
WorkingDirectory=/opt/darechy/backend
ExecStart=/opt/darechy/backend/.venv/bin/python -m app.bot.telegram
Restart=always
RestartSec=5
User=darechy

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now darechy-api darechy-bot
journalctl -u darechy-bot -f      # живой лог
```

Ollama на сервере тоже должна быть запущена как сервис. На Windows вместо systemd можно использовать
Планировщик заданий или NSSM.

## 10. Что делать, если не работает

| Симптом | Причина и решение |
|---|---|
| Бот сразу выходит с «Укажите TELEGRAM_BOT_TOKEN и TELEGRAM_API_KEY» | Запускаете не из `backend/` или поля в `.env` пустые. |
| `Unauthorized` от Telegram | Токен от BotFather скопирован с ошибкой (пробелы, лишние символы) или отозван. |
| `Conflict: terminated by other getUpdates request` | Запущено две копии бота с одним токеном. Оставьте одну (важно при переносе на сервер). |
| «База знаний сейчас недоступна» | API не запущен, неверный порт в `TELEGRAM_API_BASE_URL`, ключ отозван (в логе API будет 401) или запрос не уложился в 90 с — модель ещё не прогрелась. |
| 401 «Требуется действующий API-ключ» | В `TELEGRAM_API_KEY` не ключ из админки (не путать с `ADMIN_TOKEN`) или он отозван. |
| Ответ «нет подходящего источника» на очевидный вопрос | В базе нет нужного акта: см. [kb_corpus/README.md](../kb_corpus/README.md). То же самое будет и на сайте. |
| «Не удалось распознать аудио» | Нет ffmpeg в PATH, не установлен `faster-whisper` (шаг 7) или API не перезапущен после установки. Смотрите лог API. |
| Ответ приходит только текстом, без голоса | Нет `torch` или ffmpeg с кодеком Opus, либо ответ — отказ (их не озвучиваем), либо язык не русский. |
| Очень медленно на слабом ПК | Модель чата и эмбеддинги не помещаются в память. См. [локальная-модель.md](локальная-модель.md), параметры `CHAT_KEEP_ALIVE` и `EMBED_KEEP_ALIVE`. |

## 11. Безопасность и приватность

- Токен бота, `ADMIN_TOKEN` и API-ключ хранятся только в `backend/.env`. Никогда не коммитьте их.
- Если бот и API стоят на разных машинах, пускайте трафик через HTTPS (обратный прокси nginx/Caddy), иначе API-ключ
  уходит по сети в открытом виде.
- Бот не хранит тексты и аудио на диске сам; голос уходит в память и во временную папку голосового шлюза API.
- Перед публичным запуском прогните `python3 eval/run_eval.py` из корня репозитория: это контроль качества ответов.

## 12. Как добавить команды или новое поведение

Всё поведение бота живёт в `build_dispatcher()` в `backend/app/bot/telegram.py`: один обработчик на «старт», один на текст,
один на голос. Новую команду (например `/help`) добавляйте рядом с `on_start` из тех же декораторов `@dp.message(...)`,
а тест — в `backend/tests/test_telegram.py`. Промпт и поведение модели меняются не здесь, а в `backend/app/chat/`: так они
одинаково применяются и на сайте, и в боте.
