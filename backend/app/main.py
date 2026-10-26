import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="ДаРэчы API",
    version="0.1.0",
    description="ИИ-проводник по государственным услугам: портал, Telegram-бот, киоск.",
)

# Dev-режим: фронтенд на Vite (:5173) ходит в API напрямую через свой прокси,
# но CORS оставляем на случай, когда стучимся без прокси.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICES_FILE = Path(__file__).parent / "data" / "services_stub.json"
# Неделя 2: здесь появится загрузка каталога из Postgres, наполненного из kb_corpus.
_SERVICES: list[dict] = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    """Живость API: docker healthcheck и мониторинг."""
    return {"status": "ok", "env": settings.app_env, "version": app.version}


@app.get("/api/services")
def list_services() -> list[dict]:
    """Каталог услуг категории MVP («при рождении ребёнка»). Пока стаб на JSON."""
    return _SERVICES


@app.get("/api/services/{service_id}")
def get_service(service_id: str) -> dict:
    for service in _SERVICES:
        if service["id"] == service_id:
            return service
    raise HTTPException(status_code=404, detail="Услуга не найдена")
