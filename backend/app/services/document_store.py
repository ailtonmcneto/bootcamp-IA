import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from uuid import UUID, uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredChunk:
    page: int
    chunk_index: int
    text: str
    embedding: list[float]


@dataclass(frozen=True)
class ScoredChunk:
    page: int
    chunk_index: int
    text: str
    score: float
    document_id: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class StoredDocument:
    document_id: UUID
    filename: str
    pages: int
    characters: int
    user_id: str


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Os vetores precisam ter a mesma dimensão.")
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / norm


def filter_relevant(
    scored: list[ScoredChunk], limit: int, min_similarity: float
) -> list[ScoredChunk]:
    relevant = [chunk for chunk in scored if chunk.score >= min_similarity]
    relevant.sort(key=lambda chunk: (-chunk.score, chunk.chunk_index))
    return relevant[:limit]


def rank_by_similarity(
    chunks: list[StoredChunk],
    query_embedding: list[float],
    limit: int,
    min_similarity: float,
) -> list[ScoredChunk]:
    scored = [
        ScoredChunk(
            page=chunk.page,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=cosine_similarity(chunk.embedding, query_embedding),
        )
        for chunk in chunks
    ]
    return filter_relevant(scored, limit, min_similarity)


class DocumentStore(Protocol):
    def add(
        self,
        filename: str,
        pages: int,
        characters: int,
        chunks: list[StoredChunk],
        content: bytes,
        user_id: str,
    ) -> StoredDocument: ...

    def get(self, document_id: UUID, user_id: str) -> StoredDocument | None: ...

    def search(
        self,
        document_id: UUID,
        user_id: str,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[ScoredChunk]: ...

    def delete(self, document_id: UUID, user_id: str) -> bool: ...


class MemoryDocumentStore:
    """Armazenamento temporário em memória; o PDF original não é guardado."""

    def __init__(self) -> None:
        self._documents: dict[UUID, StoredDocument] = {}
        self._chunks: dict[UUID, list[StoredChunk]] = {}

    def add(
        self,
        filename: str,
        pages: int,
        characters: int,
        chunks: list[StoredChunk],
        content: bytes,
        user_id: str,
    ) -> StoredDocument:
        document = StoredDocument(
            document_id=uuid4(), filename=filename, pages=pages, characters=characters, user_id=user_id
        )
        self._documents[document.document_id] = document
        self._chunks[document.document_id] = chunks
        return document

    def get(self, document_id: UUID, user_id: str) -> StoredDocument | None:
        document = self._documents.get(document_id)
        return document if document and document.user_id == user_id else None

    def search(
        self,
        document_id: UUID,
        user_id: str,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[ScoredChunk]:
        if self.get(document_id, user_id) is None:
            return []
        chunks = self._chunks.get(document_id)
        if chunks is None:
            return []
        return rank_by_similarity(chunks, query_embedding, limit, min_similarity)

    def delete(self, document_id: UUID, user_id: str) -> bool:
        if self.get(document_id, user_id) is None:
            return False
        self._chunks.pop(document_id, None)
        return self._documents.pop(document_id, None) is not None


class SupabaseDocumentStore:
    """Metadados e trechos no Postgres (pgvector) e o PDF original no Storage."""

    _INSERT_BATCH = 500

    def __init__(self, url: str, service_role_key: str, bucket: str) -> None:
        from supabase import create_client

        self._client = create_client(url, service_role_key)
        self._bucket = bucket

    @staticmethod
    def _storage_path(document_id: UUID) -> str:
        return f"{document_id}.pdf"

    def add(
        self,
        filename: str,
        pages: int,
        characters: int,
        chunks: list[StoredChunk],
        content: bytes,
        user_id: str,
    ) -> StoredDocument:
        document = StoredDocument(
            document_id=uuid4(), filename=filename, pages=pages, characters=characters, user_id=user_id
        )
        document_id = str(document.document_id)
        storage_path = self._storage_path(document.document_id)
        self._client.storage.from_(self._bucket).upload(
            storage_path, content, {"content-type": "application/pdf"}
        )
        try:
            self._client.table("documents").insert(
                {
                    "id": document_id,
                    "user_id": user_id,
                    "filename": filename,
                    "storage_path": storage_path,
                    "pages": pages,
                    "characters": characters,
                }
            ).execute()
            rows = [
                {
                    "document_id": document_id,
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page,
                    "content": chunk.text,
                    "embedding": chunk.embedding,
                }
                for chunk in chunks
            ]
            for start in range(0, len(rows), self._INSERT_BATCH):
                self._client.table("chunks").insert(rows[start : start + self._INSERT_BATCH]).execute()
        except Exception:
            self._remove(document.document_id)
            raise
        return document

    def get(self, document_id: UUID, user_id: str) -> StoredDocument | None:
        response = (
            self._client.table("documents")
            .select("id, user_id, filename, pages, characters")
            .eq("id", str(document_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        row = response.data[0]
        return StoredDocument(
            document_id=UUID(row["id"]),
            filename=row["filename"],
            pages=row["pages"],
            characters=row["characters"],
            user_id=row["user_id"],
        )

    def search(
        self,
        document_id: UUID,
        user_id: str,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[ScoredChunk]:
        response = self._client.rpc(
            "match_chunks",
            {
                "query_embedding": query_embedding,
                "doc_id": str(document_id),
                "match_count": limit,
            },
        ).execute()
        scored = [
            ScoredChunk(
                page=row["page"],
                chunk_index=row["chunk_index"],
                text=row["content"],
                score=row["similarity"],
            )
            for row in response.data or []
        ]
        return filter_relevant(scored, limit, min_similarity)

    def delete(self, document_id: UUID, user_id: str) -> bool:
        if self.get(document_id, user_id) is None:
            return False
        self._remove(document_id)
        return True

    def _remove(self, document_id: UUID) -> None:
        # A FK com on delete cascade apaga os trechos junto com o documento.
        self._client.table("documents").delete().eq("id", str(document_id)).execute()
        try:
            self._client.storage.from_(self._bucket).remove([self._storage_path(document_id)])
        except Exception as exc:
            logger.warning("Falha ao remover o PDF do Storage: %s", type(exc).__name__)


@lru_cache
def get_document_store() -> DocumentStore:
    if settings.storage_backend == "supabase":
        return SupabaseDocumentStore(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_bucket,
        )
    return MemoryDocumentStore()
