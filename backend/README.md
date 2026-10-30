# DocuMind — backend

O DocuMind é um chatbot que recebe **vários PDFs por conversa** e responde perguntas usando
**somente** o conteúdo desses arquivos. Cada resposta traz marcadores `[n]` que apontam para o
trecho, o arquivo e a página que a fundamentam. Quando os documentos não têm base para responder,
a resposta é sempre:

> Não encontrei informações sobre isso no documento enviado.

Esta pasta contém a API (FastAPI + Python 3.12), que também serve o front-end estático de
`../frontend` em `/`.

## 1. Chave do Gemini e arquivo `.env`

1. Acesse <https://aistudio.google.com/apikey> e entre com uma conta Google.
2. Clique em **Create API key**, escolha (ou crie) um projeto do Google Cloud e copie a chave.
3. Dentro de `backend/`, copie o modelo de configuração:

   ```bash
   cp .env.example .env          # PowerShell: Copy-Item .env.example .env
   ```

4. Preencha a chave em `.env`:

   ```dotenv
   DOCUMIND_GEMINI_API_KEY=cole-a-chave-aqui
   ```

O `.env` está no `.gitignore`: nunca faça commit da chave. As demais variáveis já vêm com
valores padrão:

| Variável | Padrão | Uso |
| --- | --- | --- |
| `DOCUMIND_MAX_FILE_SIZE` | `10485760` | Tamanho máximo do PDF (10 MiB) |
| `DOCUMIND_MAX_PAGES` | `100` | Número máximo de páginas |
| `DOCUMIND_CHUNK_SIZE` / `DOCUMIND_CHUNK_OVERLAP` | `1000` / `150` | Tamanho e sobreposição dos trechos, em caracteres |
| `DOCUMIND_MAX_SEARCH_RESULTS` | `5` | Trechos enviados ao modelo |
| `DOCUMIND_MIN_SIMILARITY` | `0.55` | Similaridade mínima; abaixo dela o Gemini nem é chamado |
| `DOCUMIND_GEMINI_CHAT_MODEL` | `gemini-3.6-flash` | Modelo das respostas |
| — | `nomic-ai/nomic-embed-text-v1.5` / `768` | Modelo local dos embeddings |
| `DOCUMIND_STORAGE_BACKEND` | `memory` | `memory` ou `supabase` |
| `DOCUMIND_SUPABASE_URL` / `DOCUMIND_SUPABASE_SERVICE_ROLE_KEY` / `DOCUMIND_SUPABASE_BUCKET` | — / — / `documents` | Só para o modo Supabase |

Os embeddings são gerados localmente pelo Nomic; não é necessária uma chave de API para essa
etapa. Na primeira utilização, `sentence-transformers` baixa o modelo do Hugging Face. O Gemini
continua sendo usado para gerar as respostas. Se você já tinha documentos armazenados, envie-os
novamente para substituir os embeddings antigos, que não são compatíveis com o Nomic.

## 2. Como rodar

### Instalação

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Modo memória (padrão)

Com `DOCUMIND_STORAGE_BACKEND=memory`, documentos e embeddings ficam na memória do processo.

```bash
uvicorn app.main:app --reload
```

- Interface: <http://127.0.0.1:8000/>
- Swagger: <http://127.0.0.1:8000/docs>

Reiniciar o servidor apaga todos os documentos.

### Modo Supabase

1. Crie um projeto em <https://supabase.com>.
2. No painel, abra **SQL Editor**, cole o conteúdo de
   `../supabase/migrations/0001_init_documind.sql` e execute. O script ativa o `pgvector`, cria
   as tabelas `documents` e `chunks` (com RLS ligado e sem políticas), a função `match_chunks` e
   o bucket privado `documents`. O código nunca executa SQL no Supabase: esse passo é manual.
3. Em **Project Settings → API**, copie a **Project URL** e a chave **service_role**.
4. No `.env`:

   ```dotenv
   DOCUMIND_STORAGE_BACKEND=supabase
   DOCUMIND_SUPABASE_URL=https://<projeto>.supabase.co
   DOCUMIND_SUPABASE_SERVICE_ROLE_KEY=<service_role>
   ```

5. Rode `uvicorn app.main:app --reload` normalmente. O PDF vai para o Storage e os trechos com
   embeddings vão para a tabela `chunks`; os documentos sobrevivem a reinícios.
6. Execute também `../supabase/migrations/0002_documents_user.sql` depois da migração inicial para
   associar documentos aos usuários e habilitar as políticas RLS.

A chave `service_role` ignora o RLS: use-a só no backend, nunca no front-end.

## 3. Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Verifica se a API está no ar |
| `GET` | `/config/public` | Disponibiliza URL e chave pública do Supabase, sem expor a `service_role` |
| `POST` | `/documents/upload` | Multipart, campo `file`. Devolve `201` com `document_id`, `filename`, `pages`, `characters`, `status` |
| `GET` | `/documents/{id}` | Metadados do documento |
| `POST` | `/documents/{id}/ask` | Body `{"question": "..."}`. Devolve a resposta com fontes |
| `POST` | `/documents/ask` | Body `{"question": "...", "document_ids": ["..."]}`. Busca em todos os PDFs informados |
| `DELETE` | `/documents` | Body `{"document_ids": ["..."]}`. Remove os PDFs da conversa (`204`) |
| `DELETE` | `/documents/{id}` | Remove o documento. Devolve `204` |

Na interface, escolha vários PDFs juntos. O backend recebe cada PDF pelo endpoint de upload
existente; a interface envia os IDs resultantes em cada pergunta. As fontes incluem `filename` e
`document_id` além da página e do trecho. Ao iniciar uma nova conversa, a interface remove todos
os documentos da conversa.

Exemplo:

```bash
curl -F "file=@regulamento-ficticio.pdf" http://127.0.0.1:8000/documents/upload
curl -X POST http://127.0.0.1:8000/documents/<document_id>/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Até que dia posso fazer a matrícula?"}'
```

Para consultar mais de um PDF, envie todos os IDs:

```bash
curl -X POST http://127.0.0.1:8000/documents/ask \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["<document_id_1>", "<document_id_2>"],
       "question": "Quais são os prazos?"}'
```

Resposta:

```json
{"document_id": "...", "question": "Até que dia posso fazer a matrícula?", "found": true,
 "answer": "O prazo para matrícula termina em 15 de fevereiro [1].",
 "sources": [{"ref": 1, "page": 1, "text": "...",
              "quote": "O prazo para matrícula termina em 15 de fevereiro", "score": 0.82}]}
```

- `sources` traz só os trechos citados. `text` é o trecho completo e `quote` é a frase curta que
  sustenta a resposta, sempre recortada do texto original do trecho. Cada fonte também identifica
  o arquivo (`filename` e `document_id`).
- A consulta de vários PDFs responde com `document_ids` no lugar de `document_id`; cada fonte
  indica de qual PDF e página veio.
- Citações cuja frase não aparece no trecho são descartadas; se nenhuma sobrar, a resposta vira
  `found: false`.
- Quando `found` é `false`, `answer` é a mensagem fixa e `sources` vem vazio.

### Erros

Erros do upload vêm como `{"detail": {"code": "...", "message": "..."}}`:

| HTTP | `code` | Situação |
| --- | --- | --- |
| 400 | `EMPTY_FILE` | Arquivo vazio |
| 400 | `INVALID_PDF` | Arquivo não é um PDF legível |
| 413 | `FILE_TOO_LARGE` | Acima de 10 MiB |
| 413 | `TOO_MANY_PAGES` | Acima de 100 páginas |
| 422 | `PASSWORD_PROTECTED` | PDF protegido por senha |
| 422 | `NO_EXTRACTABLE_TEXT` | PDF sem camada de texto (sem OCR) |

Upload e pergunta também podem devolver:

| HTTP | Corpo | Situação |
| --- | --- | --- |
| 404 | `{"detail": "Documento não encontrado."}` | Documento inexistente |
| 429 | `{"detail": {"code": "RATE_LIMITED", ...}}` | Gemini continuou devolvendo 429 após as novas tentativas |
| 502 | `{"detail": "O serviço de IA não respondeu. Tente de novo."}` | Falha no Gemini |

As chamadas ao Gemini fazem até 2 novas tentativas (espera de 1s e depois 2s) só para erros
temporários: 5xx, 429, timeout e falha de conexão. Chave inválida e outros 4xx falham na hora.

## 4. Onde fica cada etapa do RAG

Todas em `app/services/`:

| Etapa | Arquivo |
| --- | --- |
| Extrair texto por página (PyMuPDF) | `pdf_reader.py` |
| Dividir em trechos sem cortar palavras | `document_search.py` (`split_into_chunks`) |
| Gerar embeddings (`nomic-embed-text-v1.5`, 768 dimensões) | `embeddings.py` |
| Guardar e buscar por similaridade de cosseno (memória ou Supabase/pgvector) | `document_store.py` |
| Montar o prompt, chamar o Gemini e validar as citações | `ai_service.py` |
| Novas tentativas e tratamento de 429 | `gemini_retry.py` |

As rotas ficam em `app/api/routes/documents.py` e a configuração em `app/core/config.py`.

Fluxo de uma pergunta: embedding da pergunta → busca dos trechos mais parecidos do documento →
se nenhum passar de `DOCUMIND_MIN_SIMILARITY`, devolve a mensagem fixa sem chamar o Gemini →
senão, envia os trechos numerados ao Gemini, que responde no schema
`{found, answer, citations: [{ref, quote}]}` → o backend confere cada `quote` contra o trecho,
descarta as inválidas e renumera os marcadores.

## 5. Testes

```bash
cd backend
pytest
```

Os testes usam embedder e answerer falsos e o store em memória, sem rede. Os testes marcados com
`@pytest.mark.integration` chamam o Gemini real e são pulados quando `DOCUMIND_GEMINI_API_KEY`
não está definida. Para rodar só eles: `pytest -m integration`.

Os seis testes essenciais do bootcamp:

| Caso | Teste |
| --- | --- |
| 1. Pergunta explícita: `found=true` e fonte na página certa | `tests/test_ai_service.py::test_answer_with_source` |
| 2. Paráfrase (integração com o Gemini real) | `tests/test_ai_service.py::test_paraphrased_question_against_real_gemini` |
| 3. Sem resposta: mensagem fixa e LLM não chamado abaixo do limiar | `tests/test_ai_service.py::test_below_threshold_skips_llm` |
| 4. Arquivo vazio ou inválido: 400 | `tests/test_documents.py::test_invalid_upload_and_empty_pdf`, `test_rejects_non_pdf_signature` |
| 5. Acima do limite: 413 por tamanho e por páginas | `tests/test_documents.py::test_upload_size_limit_accepts_exact_limit_and_rejects_one_byte_over`, `test_rejects_pdf_above_page_limit` |
| 6. A fonte contém o texto que sustenta a resposta | `tests/test_ai_service.py::test_source_contains_text_supporting_the_answer` |

## 6. Limitações do MVP

- Um PDF por sessão; cada pergunta é respondida de forma independente, sem usar as perguntas
  anteriores como contexto.
- Sem OCR: PDFs escaneados (só imagem) são recusados com `NO_EXTRACTABLE_TEXT`.
- Limites de 10 MiB e 100 páginas.
- Tabelas, colunas e layouts complexos podem ser extraídos fora de ordem pelo PyMuPDF.
- Sem autenticação: quem tiver o `document_id` consegue consultar ou apagar o documento. Use
  apenas PDFs públicos ou fictícios.
- No modo memória, reiniciar o servidor perde os documentos. No modo Supabase, os documentos não
  expiram sozinhos: apague-os com `DELETE /documents/{id}`.
- O limiar de similaridade é fixo: perguntas muito vagas podem receber a mensagem fixa mesmo
  quando o documento trata do assunto.
- A camada gratuita do Gemini tem limite de requisições por minuto; ao estourar, a API devolve
  `429 RATE_LIMITED`.

## 7. Roteiro da demo

1. Gere um PDF fictício (com o ambiente virtual ativo, dentro de `backend/`):

   ```bash
   python -c "import fitz; d = fitz.open(); p = d.new_page(); p.insert_text((72, 72), 'Regulamento ficticio da Escola Horizonte.'); p.insert_text((72, 100), 'O prazo para matricula termina em 15 de fevereiro.'); d.new_page().insert_text((72, 72), 'A cantina funciona das 7h as 15h em dias letivos.'); d.save('regulamento-ficticio.pdf')"
   ```

2. Rode `uvicorn app.main:app --reload` e abra <http://127.0.0.1:8000/>.
3. Envie `regulamento-ficticio.pdf`. A tela mostra o nome do arquivo e o número de páginas (2).
4. **Pergunta com resposta:** "Até que dia posso fazer a matrícula?"
   - Esperado: resposta citando 15 de fevereiro com o marcador `[1]`, e a fonte 1 apontando a
     página 1 com a frase "O prazo para matricula termina em 15 de fevereiro" destacada.
   - Destaque na apresentação: a pergunta não usa as palavras "prazo" nem "termina", e mesmo
     assim a busca semântica encontra o trecho.
5. **Pergunta sem resposta:** "Qual é o valor da mensalidade?"
   - Esperado: "Não encontrei informações sobre isso no documento enviado.", sem fontes.
6. (Opcional) Envie um arquivo `.txt` renomeado para `.pdf` para mostrar o erro `INVALID_PDF`.
