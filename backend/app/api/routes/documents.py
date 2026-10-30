import logging
from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.auth import get_current_user
from app.core.config import settings
from app.schemas.document import (
    DocumentIdsRequest,
    DocumentMetadata,
    DocumentUploadResponse,
    MultiDocumentQuestionRequest,
    MultiDocumentQuestionResponse,
    QuestionRequest,
    QuestionResponse,
    SourceExcerpt,
)
from app.services.ai_service import (
    AIServiceError,
    Answerer,
    AnswerResult,
    answer_question,
    get_answerer,
)
from app.services.document_search import split_into_chunks
from app.services.document_store import (
    DocumentStore,
    ScoredChunk,
    StoredChunk,
    StoredDocument,
    get_document_store,
)
from app.services.embeddings import Embedder, EmbeddingError, get_embedder
from app.services.gemini_retry import RateLimitedError
from app.services.pdf_reader import (
    PDFPasswordError,
    PDFReaderError,
    PDFTooManyPagesError,
    extract_text,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

AI_UNAVAILABLE = "O serviço de IA não respondeu. Tente de novo."
RATE_LIMITED_MESSAGE = "Limite de uso da IA atingido. Aguarde um minuto e tente de novo."
MAX_FILE_SIZE_MESSAGE = "O arquivo excede o limite máximo permitido de 10 MiB."


def _upload_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RateLimitedError):
        return HTTPException(
            status_code=429, detail={"code": "RATE_LIMITED", "message": RATE_LIMITED_MESSAGE}
        )
    return HTTPException(status_code=502, detail=AI_UNAVAILABLE)


def _parse_id(document_id: str) -> UUID:
    try:
        return UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Documento não encontrado.") from exc


def _answer_documents(
    question: str,
    documents: list[StoredDocument],
    user_id: str,
    embedder: Embedder,
    answerer: Answerer,
    document_store: DocumentStore,
) -> AnswerResult:
    question = question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="A pergunta não pode estar vazia.")
    try:
        query_embedding = embedder.embed_query(question)
    except EmbeddingError as exc:
        logger.warning("Falha ao gerar embedding da pergunta: %s", exc)
        raise _ai_error(exc) from exc

    chunks: list[ScoredChunk] = []
    for document in documents:
        chunks.extend(
            replace(
                chunk,
                document_id=str(document.document_id),
                filename=document.filename,
            )
            for chunk in document_store.search(
                document.document_id,
                user_id,
                query_embedding,
                limit=settings.max_search_results,
                min_similarity=settings.min_similarity,
            )
        )
    chunks.sort(key=lambda chunk: (-chunk.score, chunk.chunk_index))
    chunks = chunks[: settings.max_search_results]

    try:
        return answer_question(question, chunks, answerer)
    except AIServiceError as exc:
        logger.warning("Falha ao gerar a resposta: %s", exc)
        raise _ai_error(exc) from exc


def _source_excerpts(result: AnswerResult) -> list[SourceExcerpt]:
    return [
        SourceExcerpt(
            ref=item.ref,
            page=item.page,
            text=item.text,
            quote=item.quote,
            score=item.score,
            document_id=item.document_id,
            filename=item.filename,
        )
        for item in result.citations
    ]


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    embedder: Embedder = Depends(get_embedder),
    document_store: DocumentStore = Depends(get_document_store),
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo não informado.")
    content = bytearray()
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > settings.max_file_size:
            raise _upload_error(413, "FILE_TOO_LARGE", MAX_FILE_SIZE_MESSAGE)
    if not content:
        raise _upload_error(400, "EMPTY_FILE", "O arquivo está vazio.")
    try:
        extraction = extract_text(bytes(content), max_pages=settings.max_pages)
    except PDFTooManyPagesError as exc:
        raise _upload_error(413, "TOO_MANY_PAGES", str(exc)) from exc
    except PDFPasswordError as exc:
        raise _upload_error(422, "PASSWORD_PROTECTED", str(exc)) from exc
    except PDFReaderError as exc:
        raise _upload_error(400, "INVALID_PDF", str(exc)) from exc
    if not extraction.has_text:
        raise _upload_error(
            422,
            "NO_EXTRACTABLE_TEXT",
            "O documento não contém texto extraível. Se ele for composto por imagens, será necessário utilizar OCR.",
        )
    text_chunks = split_into_chunks(
        extraction.page_texts, settings.chunk_size, min(settings.chunk_overlap, settings.chunk_size - 1)
    )
    try:
        embeddings = embedder.embed_documents([chunk.text for chunk in text_chunks])
    except EmbeddingError as exc:
        logger.warning("Falha ao gerar embeddings do documento: %s", exc)
        raise _ai_error(exc) from exc
    document = document_store.add(
        filename=file.filename,
        pages=extraction.pages,
        characters=extraction.characters,
        chunks=[
            StoredChunk(page=chunk.page, chunk_index=index, text=chunk.text, embedding=embedding)
            for index, (chunk, embedding) in enumerate(zip(text_chunks, embeddings))
        ],
        content=bytes(content),
        user_id=user_id,
    )
    return DocumentUploadResponse(
        document_id=str(document.document_id),
        filename=document.filename,
        pages=document.pages,
        characters=document.characters,
        status="processed",
    )


@router.get("/{document_id}", response_model=DocumentMetadata)
def get_document(
    document_id: str,
    user_id: str = Depends(get_current_user),
    document_store: DocumentStore = Depends(get_document_store),
) -> DocumentMetadata:
    document = document_store.get(_parse_id(document_id), user_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return DocumentMetadata(
        document_id=str(document.document_id),
        filename=document.filename,
        pages=document.pages,
        characters=document.characters,
        status="processed",
    )


@router.post("/{document_id}/ask", response_model=QuestionResponse)
def ask_document(
    document_id: str,
    request: QuestionRequest,
    user_id: str = Depends(get_current_user),
    embedder: Embedder = Depends(get_embedder),
    answerer: Answerer = Depends(get_answerer),
    document_store: DocumentStore = Depends(get_document_store),
) -> QuestionResponse:
    document = document_store.get(_parse_id(document_id), user_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    question = request.question.strip()
    result = _answer_documents(question, [document], user_id, embedder, answerer, document_store)
    return QuestionResponse(
        document_id=str(document.document_id),
        question=question,
        found=result.found,
        answer=result.answer,
        sources=_source_excerpts(result),
    )


@router.post("/ask", response_model=MultiDocumentQuestionResponse)
def ask_documents(
    request: MultiDocumentQuestionRequest,
    user_id: str = Depends(get_current_user),
    embedder: Embedder = Depends(get_embedder),
    answerer: Answerer = Depends(get_answerer),
    document_store: DocumentStore = Depends(get_document_store),
) -> MultiDocumentQuestionResponse:
    document_ids = list(dict.fromkeys(_parse_id(item) for item in request.document_ids))
    documents = [document_store.get(item, user_id) for item in document_ids]
    if any(document is None for document in documents):
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    available_documents = [document for document in documents if document is not None]
    question = request.question.strip()
    result = _answer_documents(
        question, available_documents, user_id, embedder, answerer, document_store
    )
    return MultiDocumentQuestionResponse(
        document_ids=[str(document.document_id) for document in available_documents],
        question=question,
        found=result.found,
        answer=result.answer,
        sources=_source_excerpts(result),
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_documents(
    request: DocumentIdsRequest,
    user_id: str = Depends(get_current_user),
    document_store: DocumentStore = Depends(get_document_store),
) -> None:
    document_ids = list(dict.fromkeys(_parse_id(item) for item in request.document_ids))
    documents = [document_store.get(item, user_id) for item in document_ids]
    if any(document is None for document in documents):
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    for document_id in document_ids:
        if not document_store.delete(document_id, user_id):
            raise HTTPException(status_code=404, detail="Documento não encontrado.")


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    user_id: str = Depends(get_current_user),
    document_store: DocumentStore = Depends(get_document_store),
) -> None:
    if not document_store.delete(_parse_id(document_id), user_id):
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
