# DevTutor Bot API

A FastAPI backend for **DevTutor Bot**, a tutor that answers Angular and TypeScript questions in Spanish. It uses Retrieval-Augmented Generation (RAG): each question is matched against a local knowledge base built from the official Angular and TypeScript documentation, and the most relevant passages are sent to a small LLM, which answers from them.

It's designed to be called by a separate React frontend.

## How it works

1. **At startup**, every Markdown file in `docs_angular/` is split into ~500-character chunks, embedded with the multilingual [`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) through the Hugging Face Inference API, and indexed in an in-memory FAISS store. The multilingual model lets Spanish questions match the English docs.
2. **On each question**, the 2 most similar chunks are retrieved and passed, with the question, to [`Qwen/Qwen3-4B-Instruct-2507`](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) through [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers). The prompt tells the model to answer only from the provided docs.
3. **The response** includes the answer and the chunks it was based on.

Built with FastAPI, LangChain (`langchain-classic`, `langchain-huggingface`, `langchain-community`), and FAISS. Both models run remotely on Hugging Face, so the app needs no GPU or PyTorch and uses about 130 MB of RAM.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A [Hugging Face access token](https://huggingface.co/settings/tokens) with permission to make calls to Inference Providers (a read token is enough). It's used for both the embeddings and the LLM. Usage beyond Hugging Face's monthly free credits is billed to your account.

## Getting started

```bash
uv sync
```

Create `src/.env` from the template and set your token (`src/.env` is git-ignored; never commit it):

```bash
cp src/.env.example src/.env
# then edit src/.env: HF_API_KEY=hf_...
```

Start the dev server from the repository root:

```bash
uv run fastapi dev
```

The API runs at http://127.0.0.1:8000, with interactive docs at http://127.0.0.1:8000/docs. Startup takes a few seconds while the docs are embedded through the Hugging Face API; the server refuses to start without `HF_API_KEY`.

## API

### `POST /api/ask`

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "¿Qué es linkedSignal y cuándo lo uso?"}'
```

```json
{
  "respuesta": "`linkedSignal` es una función que crea un signal cuyo valor está ligado a otro estado...",
  "fuentes": ["`linkedSignal` works similarly to `signal` with one key difference...", "..."]
}
```

| Field | Description |
|---|---|
| `pregunta` | The user's question (request). |
| `respuesta` | The model's answer (response). |
| `fuentes` | The documentation chunks the answer was based on (response). |

Errors return HTTP 500 with a generic Spanish message in `detail`; the full error is logged on the server.

### `GET /api/health`

Returns `{"status": "online", "service": "DevTutor Bot API"}`. It only checks that the server is up, not the LLM provider.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `HF_API_KEY` | Yes | Hugging Face access token used to call the LLM. |
| `CORS_ORIGINS` | No | Comma-separated origins allowed to call the API from a browser, e.g. `https://app.example.com`. Must match exactly (scheme, host, and port). Defaults to `http://localhost:5173,http://localhost:3000` (Vite and Create React App dev servers). |

Locally, these are read from `src/.env`, which is git-ignored. In Docker, pass them with `-e`.

## Docker

```bash
docker build -t api-dev-tutor-bot .
docker run -d -p 8000:8000 \
  -e HF_API_KEY=hf_... \
  -e CORS_ORIGINS=https://your-frontend.com \
  api-dev-tutor-bot
```

The image (~650 MB) includes the `docs_angular/` knowledge base, runs as a non-root user, listens on `$PORT` if set (otherwise 8000), and has a health check on `/api/health`. `.env` is never copied into the image.

## Deploy to Render (free)

`render.yaml` defines a Docker web service on Render's free plan (0.1 CPU, 512 MB RAM; the app uses ~130 MB).

1. Sign in at https://dashboard.render.com with GitHub.
2. Click **New > Blueprint** and select this repository.
3. Enter your Hugging Face read token for `HF_API_KEY` when prompted, then deploy.
4. Once live, the API is at `https://<service-name>.onrender.com` (check `/api/health`).
5. Optionally, add `CORS_ORIGINS` with your frontend's URL under the service's **Environment** settings.

Every push to `main` redeploys automatically. On the free plan, the service sleeps after 15 minutes without traffic; the next request waits for it to wake up and embed the docs again (about a minute or more).

## Knowledge base

The bot answers from the Markdown files in `docs_angular/`:

| File | Source | Covers |
|---|---|---|
| `signals_guide.md` | [angular.dev](https://angular.dev/guide/signals) | `signal`, `computed`, reactive contexts, `linkedSignal`, `resource`, effects, signal and model inputs |
| `standalone_components_guide.md` | [angular.dev](https://angular.dev/guide/components) | Component anatomy and imports, NgModule interop, where to register providers, lazy loading, migrating to standalone |
| `rxjs_guide.md` | [angular.dev](https://angular.dev/ecosystem/rxjs-interop) | `toSignal`, `toObservable`, `rxResource`, `takeUntilDestroyed`, `HttpClient` Observables, interceptors |
| `typescript_best_practices_guide.md` | [typescriptlang.org](https://www.typescriptlang.org/docs/) | Do's and Don'ts, strict mode, everyday types, narrowing, generics guidelines, utility types |

Each guide is a curated selection of the official documentation, converted to plain Markdown with the text unchanged. Each file's header records the exact source commit and license.

To add a topic, drop a `.md` file into `docs_angular/` and restart the server. The server fails to start if the folder is missing or empty.

### Licenses

- Angular documentation: Copyright (c) 2010-2026 Google LLC, [MIT License](https://angular.dev/license).
- TypeScript documentation: Copyright (c) Microsoft Corporation, [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). The TypeScript guide's header describes the changes made to the original.

## Known limitations

- **Retrieval isn't perfect across languages.** The multilingual embeddings help Spanish questions find the English docs, but some questions still miss the right section.
- **Only 2 chunks are retrieved per question**, which is sometimes too little context. The model can then answer with wrong details, such as incorrect syntax.
- **Doc changes need a restart**; the index is built once at startup and kept in memory.
- **Startup depends on Hugging Face**: the docs are embedded through the API on every start, so the server can't start if the API is unreachable.
