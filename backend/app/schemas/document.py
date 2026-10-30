from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    pages: int
    characters: int
    status: str


class DocumentMetadata(DocumentUploadResponse):
    pass


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class MultiDocumentQuestionRequest(QuestionRequest):
    document_ids: list[str] = Field(..., min_length=1)


class DocumentIdsRequest(BaseModel):
    document_ids: list[str] = Field(..., min_length=1)


class SourceExcerpt(BaseModel):
    ref: int
    page: int
    text: str
    quote: str
    score: float
    document_id: str | None = None
    filename: str | None = None


class QuestionResponse(BaseModel):
    document_id: str
    question: str
    found: bool
    answer: str
    sources: list[SourceExcerpt]


class MultiDocumentQuestionResponse(BaseModel):
    document_ids: list[str]
    question: str
    found: bool
    answer: str
    sources: list[SourceExcerpt]
