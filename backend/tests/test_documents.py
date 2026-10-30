import json

import fitz
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.embeddings import EmbeddingError, get_embedder

client = TestClient(app)


def upload(pdf_bytes: bytes) -> dict:
    return client.post(
        "/documents/upload",
        files={"file": ("regulamento.pdf", pdf_bytes, "application/pdf")},
    ).json()


def create_pdf(text: str) -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


class FailingEmbedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("falha simulada")

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingError("falha simulada")


def test_upload_metadata_and_question(pdf_bytes: bytes) -> None:
    uploaded = upload(pdf_bytes)
    assert uploaded["status"] == "processed"
    document_id = uploaded["document_id"]
    metadata = client.get(f"/documents/{document_id}")
    assert metadata.status_code == 200
    assert "characters" in metadata.json()
    answer = client.post(
        f"/documents/{document_id}/ask", json={"question": "Qual o prazo para matrícula?"}
    )
    assert answer.status_code == 200
    sources = answer.json()["sources"]
    assert [source["page"] for source in sources] == [1]
    assert "15 de fevereiro" in sources[0]["text"]


def test_question_without_similar_chunks_returns_no_sources(pdf_bytes: bytes) -> None:
    document_id = upload(pdf_bytes)["document_id"]
    answer = client.post(f"/documents/{document_id}/ask", json={"question": "Qual a cor do céu?"})
    assert answer.status_code == 200
    assert answer.json()["sources"] == []


def test_ask_searches_all_documents_and_labels_citations(
    pdf_bytes: bytes, fake_answerer, monkeypatch
) -> None:
    monkeypatch.setattr("app.api.routes.documents.settings.max_search_results", 10)
    monkeypatch.setattr("app.api.routes.documents.settings.min_similarity", 0.0)
    first = client.post(
        "/documents/upload",
        files={"file": ("regulamento.pdf", pdf_bytes, "application/pdf")},
    ).json()
    second = client.post(
        "/documents/upload",
        files={
            "file": (
                "calendario.pdf",
                create_pdf("A entrega dos documentos ocorre em Recife."),
                "application/pdf",
            )
        },
    ).json()
    document_ids = [first["document_id"], second["document_id"]]

    response = client.post(
        "/documents/ask",
        json={"document_ids": document_ids, "question": "prazo matrícula entrega Recife"},
    )
    assert response.status_code == 200
    chunks = fake_answerer.calls[-1][1]
    chunk_refs = {
        chunk.filename: (index + 1, chunk.text[:100])
        for index, chunk in enumerate(chunks)
    }
    assert set(chunk_refs) == {"regulamento.pdf", "calendario.pdf"}
    fake_answerer.response = json.dumps(
        {
            "found": True,
            "answer": "As fontes estão nos dois documentos [1][2].",
            "citations": [
                {"ref": ref, "quote": quote}
                for ref, quote in chunk_refs.values()
            ],
        }
    )

    answer = client.post(
        "/documents/ask",
        json={"document_ids": document_ids, "question": "prazo matrícula entrega Recife"},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["document_ids"] == document_ids
    assert {source["filename"] for source in body["sources"]} == {
        "regulamento.pdf",
        "calendario.pdf",
    }
    assert all(source["document_id"] in document_ids for source in body["sources"])


def test_delete_multiple_documents(pdf_bytes: bytes) -> None:
    document_ids = [upload(pdf_bytes)["document_id"] for _ in range(2)]
    missing_document_id = "00000000-0000-0000-0000-000000000000"
    failed_delete = client.request(
        "DELETE",
        "/documents",
        json={"document_ids": [document_ids[0], missing_document_id]},
    )
    assert failed_delete.status_code == 404
    assert client.get(f"/documents/{document_ids[0]}").status_code == 200

    response = client.request("DELETE", "/documents", json={"document_ids": document_ids})
    assert response.status_code == 204
    assert all(client.get(f"/documents/{item}").status_code == 404 for item in document_ids)


def test_multi_document_operations_reject_another_users_document(pdf_bytes: bytes) -> None:
    own_document_id = upload(pdf_bytes)["document_id"]
    app.dependency_overrides[get_current_user] = lambda: "22222222-2222-2222-2222-222222222222"
    try:
        question = client.post(
            "/documents/ask",
            json={"document_ids": [own_document_id], "question": "prazo"},
        )
        deletion = client.request(
            "DELETE", "/documents", json={"document_ids": [own_document_id]}
        )
        assert question.status_code == 404
        assert deletion.status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: "11111111-1111-1111-1111-111111111111"


def test_upload_embeds_chunks_once(pdf_bytes: bytes, fake_embedder) -> None:
    calls: list[int] = []
    original = fake_embedder.embed_documents
    fake_embedder.embed_documents = lambda texts: calls.append(len(texts)) or original(texts)
    document_id = upload(pdf_bytes)["document_id"]
    for _ in range(2):
        client.post(f"/documents/{document_id}/ask", json={"question": "prazo"})
    assert calls == [2]


def test_invalid_upload_and_empty_pdf(pdf_bytes: bytes) -> None:
    empty = client.post(
        "/documents/upload",
        files={"file": ("arquivo.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "EMPTY_FILE"
    assert client.post(
        "/documents/upload",
        files={"file": ("arquivo.pdf", b"%PDF-1.7 corrompido", "application/pdf")},
    ).status_code == 400
    document = fitz.open()
    document.new_page()
    empty_pdf = document.tobytes()
    document.close()
    assert client.post(
        "/documents/upload",
        files={"file": ("imagem.pdf", empty_pdf, "application/pdf")},
    ).status_code == 422


def test_rejects_non_pdf_signature() -> None:
    response = client.post(
        "/documents/upload",
        files={"file": ("arquivo.pdf", b"texto qualquer", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_PDF"


def test_upload_size_limit_accepts_exact_limit_and_rejects_one_byte_over(
    pdf_bytes: bytes, monkeypatch, memory_store
) -> None:
    monkeypatch.setattr("app.api.routes.documents.settings.max_file_size", len(pdf_bytes))
    accepted = client.post(
        "/documents/upload",
        files={"file": ("limite.pdf", pdf_bytes, "application/pdf")},
    )
    assert accepted.status_code == 201

    rejected = client.post(
        "/documents/upload",
        files={"file": ("acima.pdf", pdf_bytes + b"x", "application/pdf")},
    )
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert len(memory_store._documents) == 1


def test_rejects_pdf_above_page_limit() -> None:
    document = fitz.open()
    for _ in range(101):
        document.new_page().insert_text((72, 72), "Página de teste.")
    data = document.tobytes()
    document.close()
    response = client.post(
        "/documents/upload",
        files={"file": ("longo.pdf", data, "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "TOO_MANY_PAGES"


def test_embedding_failure_returns_502(pdf_bytes: bytes) -> None:
    document_id = upload(pdf_bytes)["document_id"]
    app.dependency_overrides[get_embedder] = lambda: FailingEmbedder()
    expected = "O serviço de IA não respondeu. Tente de novo."
    response = client.post(f"/documents/{document_id}/ask", json={"question": "prazo"})
    assert response.status_code == 502
    assert response.json()["detail"] == expected
    response = client.post(
        "/documents/upload",
        files={"file": ("regulamento.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == expected


def test_missing_document_and_empty_question() -> None:
    assert client.get("/documents/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post(
        "/documents/00000000-0000-0000-0000-000000000000/ask", json={"question": " "}
    ).status_code == 404


def test_delete_document(pdf_bytes: bytes) -> None:
    document_id = upload(pdf_bytes)["document_id"]
    assert client.delete(f"/documents/{document_id}").status_code == 204
    assert client.get(f"/documents/{document_id}").status_code == 404


def test_document_is_isolated_between_users(pdf_bytes: bytes) -> None:
    document_id = upload(pdf_bytes)["document_id"]
    app.dependency_overrides[get_current_user] = lambda: "22222222-2222-2222-2222-222222222222"
    try:
        assert client.get(f"/documents/{document_id}").status_code == 404
        assert client.post(
            f"/documents/{document_id}/ask", json={"question": "prazo"}
        ).status_code == 404
        assert client.delete(f"/documents/{document_id}").status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: "11111111-1111-1111-1111-111111111111"
