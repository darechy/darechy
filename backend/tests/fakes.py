"""Фейковые двойки для тестов: LLM и заглушки инфры. Сеть не используется."""

from app.kb.models import Chunk, ChunkLocation, RetrievedChunk


class FakeLlm:
    """LLM-двойка: embed константой, chat — по скрипту теста."""

    def __init__(self, chat_script=None) -> None:
        self.chat_script = list(chat_script or [])
        self.chat_calls: list[tuple[str, str]] = []

    def embed(self, text: str) -> list[float] | None:
        return [0.1] * 8

    def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        return [[0.1] * 8 for _ in texts]

    def chat(self, system_prompt: str, user_prompt: str) -> str | None:
        self.chat_calls.append((system_prompt, user_prompt))
        if not self.chat_script:
            return None
        item = self.chat_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def ping(self) -> bool:
        return True


class QdrantDown:
    """Признак того, что dense-плечо недоступно (search_dense вернёт пусто)."""


def make_chunk(chunk_id: str, text: str, *, article: str = "статья 1",
               point: str | None = None, title: str = "Тестовый акт",
               number: str = "№ 57", date: str = "30.04.2010",
               url: str = "https://pravo.by/test") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        location=ChunkLocation(article=article, point=point),
        text=text,
        doc_title=title,
        doc_number=number,
        doc_date=date,
        doc_url=url,
    )


def make_hit(chunk: Chunk, score: float = 1.0, via: str = "bm25") -> RetrievedChunk:
    return RetrievedChunk(chunk=chunk, score=score, via=via)
