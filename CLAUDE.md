# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

"DevTutor Bot API": a FastAPI RAG service that answers Angular/TypeScript questions from local Markdown docs, called by a separate React frontend. See `README.md` for API usage, configuration, and Docker.

Identifiers and comments are in English. The API field names (`pregunta`, `respuesta`, `fuentes`), the LLM prompt, and error messages are in Spanish because they're the frontend contract and user-facing text; don't translate them.

## Commands

Project is managed with `uv` (Python 3.12, see `.python-version`). Run everything from the repo root.

```bash
uv sync                                   # install deps into .venv
uv run fastapi dev                        # dev server with reload (http://127.0.0.1:8000, docs at /docs)
uv run uvicorn src.server:app --reload    # alternative
uvx ruff check --select F src/            # ad hoc lint (no ruff config in the repo)

docker build -t api-dev-tutor-bot .
docker run -p 8000:8000 -e HF_API_KEY=... -e CORS_ORIGINS=https://your-frontend.com api-dev-tutor-bot
```

There are no tests or formatter configured. To check `/api/ask` end to end, use `fastapi.testclient.TestClient` inside a `with` block (so the `lifespan` handler builds the retriever); it needs a real `HF_API_KEY` in `src/.env`.

## Project setup

- `uv` treats this as an app, not a package (`[tool.uv] package = false`): no build step, and `src/` isn't installed. `[tool.fastapi] entrypoint = "src.server:app"` is what lets `fastapi dev` run with no path.
- On Linux, `torch` resolves to the CPU-only PyTorch index (`[tool.uv.sources]`), which keeps the Docker image from pulling several GB of CUDA libraries. `torch` is a direct dependency only so this source override applies.
- Dependencies live in `pyproject.toml` (manage with `uv add`; no `requirements.txt`). On LangChain 1.x, `RetrievalQA` is a legacy chain that only exists in `langchain-classic`. `langchain-community` (used for `DirectoryLoader`/`TextLoader`/`FAISS`) is being sunset upstream and emits a `DeprecationWarning` on import.
- The Dockerfile downloads the embedding model at build time, so changing the embedding model name in `server.py` means updating the Dockerfile too. Don't set `HF_HUB_OFFLINE=1` in the image: it also blocks the LLM calls to Hugging Face.

## Architecture

`src/server.py` is the entire application.

- **Startup** (`lifespan`): `get_retriever()` loads `**/*.md` from `./docs_angular` → `RecursiveCharacterTextSplitter` (500/50) → local `all-MiniLM-L6-v2` embeddings → in-memory FAISS, top-k=2. The result is stored on `app.state.retriever`, so doc changes need a restart. `./docs_angular` is resolved relative to the **current working directory**; startup fails if it's missing or has no `.md` files.
- **Per request** (`POST /api/ask`): builds `ChatHuggingFace` wrapping a `HuggingFaceEndpoint` for `LLM_MODEL` (`Qwen/Qwen3-4B-Instruct-2507`, `provider="auto"`, served remotely by Hugging Face Inference Providers), then a `RetrievalQA` "stuff" chain with the Spanish `PROMPT`. Both are cheap to build (remote API client, no model download).
- **Config**: `load_dotenv` reads `src/.env` by absolute path. `HF_API_KEY` is passed explicitly to `HuggingFaceEndpoint`. `CORS_ORIGINS` (comma-separated, exact scheme+host+port) defaults to the Vite/CRA dev servers; credentials are disabled because the API uses no cookies or auth headers.
- **Errors**: `/api/ask` logs exceptions with `logger.exception` and returns a generic Spanish message. Don't put `str(e)` back into responses.
- Before changing `LLM_MODEL`, check the model is served by some provider at `https://router.huggingface.co/v1/models`. Prefer "Instruct" variants; "Thinking" models put reasoning blocks in the answer.

## Knowledge base (`docs_angular/`)

Curated selections of official docs, converted to plain Markdown:

- `signals_guide.md`: `signal`/`computed`, reactive contexts and `untracked`, `linkedSignal` basics, `resource`/`httpResource`, effects and when not to use them, signal and model inputs. Keeps a Spanish intro line at the top.
- `standalone_components_guide.md`: component anatomy/imports, `standalone: false` and NgModule interop, where to register providers, lazy loading with `loadComponent`, the standalone migration.
- `rxjs_guide.md`: `toSignal`/`toObservable`/`rxResource`, `takeUntilDestroyed`, `HttpClient` Observables and error handling, functional interceptors.
- `typescript_best_practices_guide.md`: Do's and Don'ts, strict mode, everyday types, narrowing, generics guidelines, `readonly`, common utility types.

Conventions for these files (follow them when adding docs):

- The prose is copied verbatim; only markup changes (site-specific tags → Markdown, relative links → absolute, `angular-ts`/`angular-html` fences → `ts`/`html`).
- Each file has one H1, then an H2 per source page followed by an `Official page: <url>` line, so retrieved chunks carry their source.
- The header blockquote records the source repo commit and the license. Angular docs (angular/angular, `adev/src/content/`) are MIT, Google LLC. The TypeScript docs (microsoft/TypeScript-Website, `packages/documentation/copy/en/`) are **CC BY 4.0**, which requires keeping the attribution, license link, and the "Changes" note.
- TypeScript "twoslash" code blocks are rendered as the site shows them: setup code above `// ---cut---` and `// @directive` / `^?` lines are removed, examples with `// @errors` get a first-line comment saying they intentionally fail to compile, and Playground links are reduced to their text.

## Known retrieval weaknesses

- The docs are in English while users ask in Spanish, and `all-MiniLM-L6-v2` is English-only, so Spanish questions often retrieve unrelated chunks. `paraphrase-multilingual-MiniLM-L12-v2` tested much better.
- `k=2` chunks of 500 chars is often too little context; the model can then fill gaps with wrong details (e.g. invented `@Input` syntax).
