import json
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.ai_service import (
    NOT_FOUND_MESSAGE,
    SYSTEM_PROMPT,
    AIServiceError,
    GeminiAnswerer,
    answer_question,
    build_user_message,
    get_answerer,
)
from app.services.document_store import ScoredChunk
from app.services.embeddings import NomicEmbedder, get_embedder

from .conftest import FakeAnswerer

client = TestClient(app)

CHUNKS = [
    ScoredChunk(page=1, chunk_index=0, text="O prazo para matrícula é 15 de fevereiro.", score=0.82),
    ScoredChunk(page=2, chunk_index=1, text="Documentos necessários para a matrícula.", score=0.61),
]


def cite(ref: int, quote: str = "") -> dict:
    return {"ref": ref, "quote": quote}


def model_json(found: bool, answer: str, citations: list[dict]) -> str:
    return json.dumps({"found": found, "answer": answer, "citations": citations})


def upload(pdf_bytes: bytes) -> str:
    return client.post(
        "/documents/upload",
        files={"file": ("regulamento.pdf", pdf_bytes, "application/pdf")},
    ).json()["document_id"]


def test_user_message_numbers_excerpts_with_page() -> None:
    message = build_user_message("Qual o prazo?", CHUNKS)
    assert message.startswith("[1] (pág. 1)\nO prazo para matrícula é 15 de fevereiro.")
    assert "[2] (pág. 2)\nDocumentos necessários" in message
    assert message.endswith("Pergunta: Qual o prazo?")


def test_answer_with_source(pdf_bytes: bytes, fake_answerer: FakeAnswerer) -> None:
    fake_answerer.response = model_json(
        True, "O prazo é 15 de fevereiro [1].", [cite(1, "prazo para matrícula é 15 de fevereiro")]
    )
    document_id = upload(pdf_bytes)
    response = client.post(
        f"/documents/{document_id}/ask", json={"question": "Qual o prazo para matrícula?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"document_id", "question", "found", "answer", "sources"}
    assert body["found"] is True
    assert body["answer"] == "O prazo é 15 de fevereiro [1]."
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert set(source) == {
        "ref",
        "page",
        "text",
        "quote",
        "score",
        "document_id",
        "filename",
    }
    assert source["ref"] == 1
    assert source["page"] == 1
    assert source["document_id"] == document_id
    assert source["filename"] == "regulamento.pdf"
    assert "15 de fevereiro" in source["text"]
    assert source["quote"] == "prazo para matrícula é 15 de fevereiro"
    assert 0 < source["score"] <= 1


def test_source_contains_text_supporting_the_answer(
    pdf_bytes: bytes, fake_answerer: FakeAnswerer
) -> None:
    fake_answerer.response = model_json(
        True, "A matrícula vai até 15 de fevereiro [1].", [cite(1, "o PRAZO para  matrícula é 15 de fevereiro")]
    )
    document_id = upload(pdf_bytes)
    body = client.post(
        f"/documents/{document_id}/ask", json={"question": "Qual o prazo para matrícula?"}
    ).json()
    assert body["found"] is True
    [source] = body["sources"]
    assert source["quote"] in source["text"]
    assert "15 de fevereiro" in source["quote"]
    assert "15 de fevereiro" in body["answer"]


def test_sources_only_include_cited_excerpts() -> None:
    answerer = FakeAnswerer(
        model_json(True, "Veja os documentos [2].", [cite(2, "Documentos necessários")])
    )
    result = answer_question("Quais documentos?", CHUNKS, answerer)
    assert result.found is True
    assert result.answer == "Veja os documentos [1]."
    assert [(item.ref, item.page) for item in result.citations] == [(1, 2)]


def test_below_threshold_skips_llm(pdf_bytes: bytes, fake_answerer: FakeAnswerer) -> None:
    document_id = upload(pdf_bytes)
    response = client.post(f"/documents/{document_id}/ask", json={"question": "Qual a cor do céu?"})
    assert response.status_code == 200
    assert response.json()["found"] is False
    assert response.json()["answer"] == NOT_FOUND_MESSAGE
    assert response.json()["sources"] == []
    assert fake_answerer.calls == []


def test_model_says_not_found() -> None:
    result = answer_question("Qual o horário?", CHUNKS, FakeAnswerer(model_json(False, "", [])))
    assert (result.found, result.answer, result.citations) == (False, NOT_FOUND_MESSAGE, [])


@pytest.mark.parametrize(
    "raw",
    [
        "isto não é JSON",
        "",
        '{"found": true, "answer": "Sem lista"}',
        '{"found": "sim", "answer": "Tipo errado [1]", "citations": [{"ref": 1, "quote": ""}]}',
        '{"found": true, "answer": "Formato antigo [1]", "citations": [1]}',
    ],
)
def test_invalid_json_returns_not_found(raw: str) -> None:
    result = answer_question("Qual o prazo?", CHUNKS, FakeAnswerer(raw))
    assert (result.found, result.answer, result.citations) == (False, NOT_FOUND_MESSAGE, [])


@pytest.mark.parametrize("refs", [[3], [0], [1, 5], [-1]])
def test_citation_out_of_range_returns_not_found(refs: list[int]) -> None:
    answerer = FakeAnswerer(model_json(True, "Resposta [3].", [cite(ref) for ref in refs]))
    result = answer_question("Qual o prazo?", CHUNKS, answerer)
    assert (result.found, result.answer, result.citations) == (False, NOT_FOUND_MESSAGE, [])


def test_found_without_citations_returns_not_found() -> None:
    result = answer_question("Qual o prazo?", CHUNKS, FakeAnswerer(model_json(True, "15/02.", [])))
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE


def test_valid_quote_ignores_case_and_spaces() -> None:
    quote = "O  PRAZO para\nmatrícula é 15 de fevereiro"
    answerer = FakeAnswerer(model_json(True, "O prazo é 15 de fevereiro [1].", [cite(1, quote)]))
    result = answer_question("Qual o prazo?", CHUNKS, answerer)
    assert result.found is True
    assert result.citations[0].quote == "O prazo para matrícula é 15 de fevereiro"
    assert result.citations[0].text == CHUNKS[0].text


def test_quote_uses_original_chunk_text() -> None:
    chunks = [ScoredChunk(page=3, chunk_index=0, text="O recurso deve ser feito em 7 dias\ncorridos.", score=0.8)]
    answerer = FakeAnswerer(model_json(True, "São 7 dias corridos [1].", [cite(1, "7 diascorridos")]))
    result = answer_question("Qual o prazo do recurso?", chunks, answerer)
    assert result.found is True
    assert result.citations[0].quote == "7 dias corridos"


def test_invented_quote_is_rejected_and_its_marker_removed() -> None:
    answerer = FakeAnswerer(
        model_json(
            True,
            "O prazo é 15 de fevereiro [1] e há documentos [2].",
            [cite(1, "O prazo é 20 de março."), cite(2, "Documentos necessários")],
        )
    )
    result = answer_question("Qual o prazo?", CHUNKS, answerer)
    assert result.found is True
    assert result.answer == "O prazo é 15 de fevereiro e há documentos [1]."
    assert [(item.ref, item.quote) for item in result.citations] == [(1, "Documentos necessários")]
    assert result.citations[0].text == CHUNKS[1].text


@pytest.mark.parametrize("markers", ["[1, 2]", "[1,2]", "[1-2]", "[1][2]"])
def test_grouped_markers_are_split(markers: str) -> None:
    answerer = FakeAnswerer(
        model_json(
            True,
            f"O prazo é 15 de fevereiro e há documentos {markers}.",
            [cite(1, "prazo para matrícula"), cite(2, "Documentos necessários")],
        )
    )
    result = answer_question("Qual o prazo?", CHUNKS, answerer)
    assert result.answer == "O prazo é 15 de fevereiro e há documentos [1][2]."
    assert [item.ref for item in result.citations] == [1, 2]


def test_brackets_outside_chunk_range_stay_intact() -> None:
    answer = "Pela norma [2024], o prazo é 15 de fevereiro [2] no ciclo [2023-2024] e no item [0]."
    answerer = FakeAnswerer(model_json(True, answer, [cite(2, "Documentos necessários")]))
    result = answer_question("Qual o prazo?", CHUNKS, answerer)
    assert result.answer == "Pela norma [2024], o prazo é 15 de fevereiro [1] no ciclo [2023-2024] e no item [0]."
    assert [item.ref for item in result.citations] == [1]


def test_sources_are_renumbered_in_order_of_appearance() -> None:
    chunks = [
        ScoredChunk(page=page, chunk_index=page - 1, text=f"Regra número {page} do edital.", score=0.7)
        for page in range(1, 5)
    ]
    answerer = FakeAnswerer(
        model_json(
            True,
            "A primeira vem da regra 4 [4] e a segunda da regra 3 [3]. Reveja a regra 4 [4].",
            [cite(3, "Regra número 3"), cite(4, "Regra número 4")],
        )
    )
    result = answer_question("Quais regras?", chunks, answerer)
    assert result.answer == "A primeira vem da regra 4 [1] e a segunda da regra 3 [2]. Reveja a regra 4 [1]."
    assert [(item.ref, item.page) for item in result.citations] == [(1, 4), (2, 3)]


def test_gemini_error_returns_502(pdf_bytes: bytes, fake_answerer: FakeAnswerer) -> None:
    fake_answerer.response = AIServiceError("falha simulada")
    document_id = upload(pdf_bytes)
    response = client.post(
        f"/documents/{document_id}/ask", json={"question": "Qual o prazo para matrícula?"}
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "O serviço de IA não respondeu. Tente de novo."


def test_gemini_answerer_config_and_errors() -> None:
    calls: list[dict] = []

    def generate_content(*, model, contents, config):
        calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(
            text=model_json(True, "O prazo é 15 de fevereiro [1].", [cite(1, "15 de fevereiro")])
        )

    fake_client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    raw = GeminiAnswerer("chave", "modelo", client=fake_client).generate("Qual o prazo?", CHUNKS)
    assert json.loads(raw)["citations"] == [cite(1, "15 de fevereiro")]
    config = calls[0]["config"]
    assert calls[0]["model"] == "modelo"
    assert config.temperature == 0.1
    assert config.response_mime_type == "application/json"
    assert config.system_instruction == SYSTEM_PROMPT
    assert set(config.response_schema.model_fields) == {"found", "answer", "citations"}
    citation_schema = config.response_schema.model_fields["citations"].annotation.__args__[0]
    assert set(citation_schema.model_fields) == {"ref", "quote"}

    def failing(**_):
        raise RuntimeError("rede")

    failing_client = SimpleNamespace(models=SimpleNamespace(generate_content=failing))
    with pytest.raises(AIServiceError):
        GeminiAnswerer("chave", "modelo", client=failing_client).generate("Qual?", CHUNKS)
    with pytest.raises(AIServiceError):
        GeminiAnswerer("", "modelo").generate("Qual?", CHUNKS)


@pytest.mark.integration
@pytest.mark.skipif(not settings.gemini_api_key, reason="DOCUMIND_GEMINI_API_KEY ausente")
def test_paraphrased_question_against_real_gemini() -> None:
    document = fitz.open()
    document.new_page().insert_text(
        (72, 72), "Regulamento fictício da Escola Horizonte.", fontsize=11
    )
    document[0].insert_text(
        (72, 100), "O prazo para matrícula termina em 15 de fevereiro.", fontsize=11
    )
    document.new_page().insert_text(
        (72, 72), "A cantina funciona das 7h às 15h em dias letivos.", fontsize=11
    )
    pdf = document.tobytes()
    document.close()

    app.dependency_overrides[get_embedder] = lambda: NomicEmbedder()
    app.dependency_overrides[get_answerer] = lambda: GeminiAnswerer(
        settings.gemini_api_key, settings.gemini_chat_model
    )
    document_id = upload(pdf)
    response = client.post(
        f"/documents/{document_id}/ask",
        json={"question": "Até que dia posso me inscrever na escola?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert "15" in body["answer"]
    assert body["sources"]
    assert all(source["page"] == 1 for source in body["sources"])
    assert any("15 de fevereiro" in source["quote"] for source in body["sources"])
