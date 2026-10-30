# DocuMind

MVP do Bootcamp IA First. O DocuMind é um chatbot que recebe **vários PDFs por conversa** e responde
perguntas usando **somente** o conteúdo desses arquivos. Cada resposta mostra o trecho, o arquivo e
a página que a fundamentam. Quando os documentos não têm base para responder, a resposta é sempre:

> Não encontrei informações sobre isso no documento enviado.

**Stack:** FastAPI (Python 3.12), PyMuPDF, Gemini (`gemini-3.6-flash` para respostas), Nomic
Embed Text v1.5 para embeddings locais, Supabase (pgvector + Storage) opcional, e front-end em
HTML + CSS + JS puro servido pela própria API.

```
backend/    API, RAG e testes (detalhes em backend/README.md)
frontend/   index.html, app.js, styles.css
supabase/   migrations/ com o SQL para rodar no painel do Supabase
```

## Chave do Gemini e `.env`

1. Acesse <https://aistudio.google.com/apikey>, entre com uma conta Google e clique em
   **Create API key**. Copie a chave.
2. Em `backend/`, copie `.env.example` para `.env` e preencha:

   ```dotenv
   DOCUMIND_GEMINI_API_KEY=cole-a-chave-aqui
   DOCUMIND_SUPABASE_URL=https://<projeto>.supabase.co
   DOCUMIND_SUPABASE_PUBLISHABLE_KEY=chave-publica
   DOCUMIND_SUPABASE_SERVICE_ROLE_KEY=chave-privada-apenas-no-backend
   ```

O `.env` não vai para o Git. A tabela de todas as variáveis está no
[backend/README.md](backend/README.md#1-chave-do-gemini-e-arquivo-env).

## Como rodar

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abra <http://127.0.0.1:8000/> (interface) ou <http://127.0.0.1:8000/docs> (Swagger).

- **Memória** (padrão, `DOCUMIND_STORAGE_BACKEND=memory`): não precisa de mais nada. Reiniciar o
  servidor apaga os documentos.
- **Supabase** (`DOCUMIND_STORAGE_BACKEND=supabase`): rode manualmente
  `supabase/migrations/0001_init_documind.sql` e depois
  `supabase/migrations/0002_documents_user.sql` no SQL Editor do projeto e preencha
  as variáveis do Supabase no `.env`. Passo a passo no
  [backend/README.md](backend/README.md#modo-supabase).

## Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Verifica se a API está no ar |
| `GET` | `/config/public` | Disponibiliza apenas a configuração pública do Supabase |
| `POST` | `/documents/upload` | Envia o PDF (multipart, campo `file`); devolve `201` com `document_id` |
| `GET` | `/documents/{id}` | Metadados do documento |
| `POST` | `/documents/{id}/ask` | `{"question": "..."}`; devolve `found`, `answer` e `sources` (página, trecho e frase citada) |
| `POST` | `/documents/ask` | `{"question": "...", "document_ids": ["..."]}`; consulta todos os PDFs da conversa |
| `DELETE` | `/documents` | `{"document_ids": ["..."]}`; encerra a conversa e remove seus PDFs |
| `DELETE` | `/documents/{id}` | Remove o documento (`204`) |

Formato das respostas e tabela de erros: [backend/README.md](backend/README.md#3-endpoints).

## Onde fica cada etapa do RAG

Em `backend/app/services/`:

| Etapa | Arquivo |
| --- | --- |
| Extrair texto por página | `pdf_reader.py` |
| Dividir em trechos | `document_search.py` (`split_into_chunks`) |
| Embeddings | `embeddings.py` |
| Guardar e buscar por similaridade | `document_store.py` (memória ou Supabase) |
| Montar prompt, gerar com Gemini e validar citações | `ai_service.py` |

As rotas ficam em `backend/app/api/routes/documents.py`.

## Testes

```bash
cd backend
pytest
```

Os testes rodam sem rede, com fakes. Os de integração (`pytest -m integration`) chamam o Gemini
real e são pulados se não houver chave no `.env`. A lista dos seis testes essenciais está no
[backend/README.md](backend/README.md#5-testes).

## Limitações do MVP

- A conversa aceita vários PDFs; cada pergunta é independente (sem memória das respostas).
- Sem OCR: PDFs escaneados são recusados. Limites de 10 MiB e 100 páginas.
- Tabelas e layouts em colunas podem ser extraídos fora de ordem.
- Sem autenticação: quem tiver o `document_id` acessa o documento. Use só PDFs públicos ou
  fictícios.
- No modo memória os documentos somem ao reiniciar; no Supabase permanecem até serem removidos ao iniciar uma nova conversa.
- Perguntas muito vagas podem não passar do limiar de similaridade e receber a mensagem fixa.
- A camada gratuita do Gemini limita requisições por minuto (a API devolve `429 RATE_LIMITED`).

## Roteiro da demo

1. Gere um PDF fictício (em `backend/`, com o ambiente virtual ativo):

   ```bash
   python -c "import fitz; d = fitz.open(); p = d.new_page(); p.insert_text((72, 72), 'Regulamento ficticio da Escola Horizonte.'); p.insert_text((72, 100), 'O prazo para matricula termina em 15 de fevereiro.'); d.new_page().insert_text((72, 72), 'A cantina funciona das 7h as 15h em dias letivos.'); d.save('regulamento-ficticio.pdf')"
   ```

2. Suba o servidor, abra <http://127.0.0.1:8000/> e envie `regulamento-ficticio.pdf`.
3. **Pergunta com resposta:** "Até que dia posso fazer a matrícula?" → resposta com 15 de
   fevereiro e marcador `[1]`; a fonte aponta a página 1 e destaca a frase do regulamento. A
   pergunta é uma paráfrase: não usa "prazo" nem "termina".
4. **Pergunta sem resposta:** "Qual é o valor da mensalidade?" → "Não encontrei informações
   sobre isso no documento enviado.", sem fontes.
