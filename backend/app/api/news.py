"""Public news and authenticated publication."""
import base64
import binascii
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.admin_routes import require_admin
from app.core.config import get_settings

news_router = APIRouter(prefix="/api")


class NewsDraft(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=50000)
    tags: str = Field(default="", max_length=300)
    photo: str = Field(default="", max_length=4_000_000)
    alt: str = Field(default="", max_length=250)


def connect():
    db = sqlite3.connect(get_settings().news_db)
    db.execute("""CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY, title TEXT, body TEXT, tags TEXT, author TEXT,
        published TEXT, photo BLOB, mime TEXT, alt TEXT)""")
    db.row_factory = sqlite3.Row
    return db


@news_router.get("/news")
def news():
    with closing(connect()) as db:
        rows = db.execute("""SELECT id,title,body,tags,author,published,alt,
                          photo IS NOT NULL AS has_photo FROM news ORDER BY id DESC""")
        return [dict(row) | {
            "tags": json.loads(row["tags"]),
            "minutes": max(1, math.ceil(len(row["body"].split()) / 200)),
        } for row in rows]


@news_router.get("/news/{news_id}/photo")
def news_photo(news_id: int):
    with closing(connect()) as db:
        row = db.execute("SELECT photo,mime FROM news WHERE id=?", (news_id,)).fetchone()
    if not row or row["photo"] is None:
        raise HTTPException(404)
    return Response(row["photo"], media_type=row["mime"],
                    headers={"X-Content-Type-Options": "nosniff"})


@news_router.post("/admin/news", status_code=201)
def publish(draft: NewsDraft, settings=Depends(require_admin),
            token: str = Header(alias="X-Admin-Token")):
    title, body = draft.title.strip(), draft.body.strip()
    if not title or not body:
        raise HTTPException(400, "Заполните заголовок и текст")
    author = next((name for name, secret in settings.admin_accounts.items()
                   if compare_digest(secret.encode(), token.encode())), "Редакция ДаРэчы")
    photo = mime = None
    if draft.photo:
        try:
            photo = base64.b64decode(draft.photo, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, "Некорректное фото") from None
        if photo.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif photo.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            raise HTTPException(400, "Загрузите JPEG или PNG")
        if not draft.alt.strip():
            raise HTTPException(400, "Добавьте описание фото")
    tags = list(dict.fromkeys(tag.strip() for tag in draft.tags.split(",") if tag.strip()))
    with closing(connect()) as db, db:
        result = db.execute("INSERT INTO news VALUES (NULL,?,?,?,?,?,?,?,?)", (
            title, body, json.dumps(tags), author, datetime.now(timezone.utc).isoformat(),
            photo, mime, draft.alt.strip(),
        ))
        return {"id": result.lastrowid}
