# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

"DevTutor Bot API": a FastAPI RAG service that answers Angular/TypeScript questions from local Markdown docs, called by a separate React frontend. See `README.md` for API usage, configuration, Docker, and the Render deploy.

Identifiers and comments are in English. The API field names (`pregunta`, `respuesta`, `fuentes`), the LLM prompt, and error messages are in Spanish because they're the frontend contract and user-facing text; don't translate them.

## Commands

Project is managed with `uv` (Python 3.12, see `.python-version`). Run everything from the repo root.

```bash
uv sync                                   # install deps into .venv
uv run fastapi dev                        # dev server with reload (http://127.0.0.1:8000, docs at /docs)
uv run uvicorn src.server:app --reload    # alternative
uvx ruff check --select F src/ scripts/   # ad hoc lint (no ruff config in the repo)

uv sync --group finetuned                 # local-only deps for fine-tuning / USE_FINETUNED=1
uv run python -m scripts.generate_dataset # build data/devtutor_sft.jsonl (needs GROQ_API_KEY)
USE_FINETUNED=1 uv run fastapi dev        # serve with the local LoRA model in models/devtutor-lora
                                          # (a plain `uv sync` removes the finetuned group again)

docker build -t api-dev-tutor-bot .
docker run -p 8000:8000 -e HF_API_KEY=... -e CORS_ORIGINS=https://your-frontend.com api-dev-tutor-bot
```

There are no tests or formatter configured. To check `/api/ask` or `/api/info` end to end, use `fastapi.testclient.TestClient` inside a `with` block (so the `lifespan` handler embeds the docs). Remote mode needs a real `HF_API_KEY` in `src/.env` **and** remaining Inference Providers credits: when they run out, every HF call returns `402 Payment Required` and startup fails. To test without credits, use local mode (`USE_FINETUNED=1` with an adapter), or monkeypatch `RemoteModels.embed` for endpoints that don't generate.

A Docker container on port 8000 serves the code baked into its image, not the working tree: rebuild and restart it after changes, or stop it and use `fastapi dev`.

## Project setup

- `uv` treats this as an app, not a package (`[tool.uv] package = false`): no build step, and `src/` isn't installed. `[tool.fastapi] entrypoint = "src.server:app"` is what lets `fastapi dev` run with no path.
- Dependencies live in `pyproject.toml` (manage with `uv add`; no `requirements.txt`). There's no RAG framework on purpose: the pipeline is written out by hand with `huggingface_hub.InferenceClient` and NumPy, mirroring the course practice notebook. `langchain-text-splitters` is the only LangChain package, used just for chunking.
- The `finetuned` dependency group (torch, transformers, peft, trl, sentence-transformers, accelerate) is local-only: the Dockerfile's `uv sync --no-dev` skips non-default groups, and `.dockerignore` excludes `models/`, `notebooks/`, `scripts/`, `data/`. Don't add it to `default-groups`. `huggingface-hub` is pinned `<2` indirectly because `transformers` 5.x requires it.
- No PyTorch or local models in the deployed app, on purpose: both embeddings and the LLM are remote Hugging Face calls. That keeps memory at ~80 MB and the image at ~490 MB, so the app fits Render's free plan (512 MB, 0.1 CPU). Never move torch/sentence-transformers/transformers into the main dependencies; check memory with `docker run --memory=512m --cpus=0.1` after dependency changes.
- Deployment: `render.yaml` (Render Blueprint, free plan, auto-deploy on push to `main`, `HF_API_KEY` entered in the dashboard via `sync: false`). The container listens on `$PORT` (Render sets it), defaulting to 8000. Hugging Face Docker Spaces were tried and now require a PRO subscription.

## Architecture

`src/server.py` is the entire application. Model access goes through two interchangeable classes with `embed(texts)` (L2-normalized rows) and `generate(prompt)`: `RemoteModels` (default; Hugging Face API) and `LocalModels` (`USE_FINETUNED=1`; `SentenceTransformer` + `AutoPeftModelForCausalLM` from `FINETUNED_ADAPTER`, merged, on MPS/CUDA/CPU, greedy decoding, a lock around `generate`). `LocalModels` imports torch lazily so remote mode never needs it. The chosen instance is `app.state.models`.

- **Startup** (`lifespan`): picks the models class, then `load_fragments()` reads `**/*.md` from `DOCS_DIR` (`./docs_angular`) → `RecursiveCharacterTextSplitter` (500/50) → `models.embed()` with `EMBEDDING_MODEL` (multilingual `paraphrase-multilingual-MiniLM-L12-v2`; remotely it's `feature_extraction` on `InferenceClient(provider="hf-inference")`, in batches of `EMBEDDING_BATCH_SIZE` because each call sends all its texts in one request). Fragments and vectors are stored on `app.state` (`fragments`, `fragment_vectors`), so doc changes need a restart. `./docs_angular` is resolved relative to the **current working directory**; startup fails if it has no `.md` files or, in remote mode, if `HF_API_KEY` isn't set. Startup takes ~7 s locally and ~1 min on Render's 0.1 CPU.
- **Per request** (`POST /api/ask` → `assistant()`): `search_fragments()` embeds the question and takes the `TOP_K` (2) fragments with the highest dot product (= cosine similarity, since vectors are normalized); the Spanish `PROMPT` is filled with them and passed to `models.generate()`: remotely, `chat_completion` on `InferenceClient(provider="auto")` to `LLM_MODEL` (`Qwen/Qwen3-4B-Instruct-2507`); locally, the fine-tuned model.
- **`GET /api/info`**: built from the live config (module constants, `app.state`, each models class's `describe()`, and `describe_adapter()`, which reads `adapter_config.json` + the notebook's `training_info.json` from `FINETUNED_ADAPTER` without torch). When adding a tunable (e.g. a new constant), expose it there too. Keep keys English and never include keys/tokens.
- **Fine-tuning** (local only): `scripts/generate_dataset.py` distills Q&A pairs from a Groq teacher (`TEACHER_MODEL`, default `qwen/qwen3.8-27b`; `GROQ_API_KEY`) with local embeddings, writing `data/devtutor_sft.jsonl` in TRL conversational prompt-completion format (plus `pregunta` and `tipo` = `docs`/`fuera_de_tema` for inspection). Groq's model list changes often (Llama 3.3/4 and Qwen3-32B are gone); check `https://api.groq.com/openai/v1/models` before changing it, and avoid reasoning models (`gpt-oss`), whose hidden reasoning eats the 8K tokens/min free-tier budget. The script retries 429s, runs 2 workers, and writes each example as it finishes, so a cut-off run keeps its output; a full run takes ~40 min. The committed dataset has 249 examples (224 docs + 25 off-topic) with 4 answers hand-cleaned of teacher meta-language ("el contexto", "el fragmento"); regenerating overwrites that. The teacher's `ANSWER_PROMPT` holds the style rules; the student is trained on the plain `PROMPT`. Prompts are built with the server's `PROMPT` and `TOP_K` retrieval, and the notebook writes `training_info.json` next to the adapter, so if you change `PROMPT` regenerate the dataset and retrain. `notebooks/fine_tuning.ipynb` (Colab, T4; downloads the dataset from `main` on GitHub, falling back to a manual upload) trains a LoRA adapter (r=16, alpha=32, all attention + MLP projections, 3 epochs, lr 2e-4) on `Qwen/Qwen2.5-1.5B-Instruct`; it was smoke-tested locally against transformers 5 / trl 1.14 (e.g. `warmup_ratio` is gone; use a float `warmup_steps`).
- **Config**: `load_dotenv` reads `src/.env` by absolute path. `HF_API_KEY` (a read token is enough) is passed explicitly to both `InferenceClient`s and is only required in remote mode. `USE_FINETUNED=1` selects local mode, `FINETUNED_ADAPTER` (default `./models/devtutor-lora`, or a Hub repo id) picks the adapter, and `GROQ_API_KEY`/`TEACHER_MODEL` are read only by the dataset script. `CORS_ORIGINS` (comma-separated, exact scheme+host+port) defaults to the production frontend (`https://frontend-dev-tutor-bot.vercel.app`) plus the Vite/CRA dev servers, and setting it replaces those defaults; credentials are disabled because the API uses no cookies or auth headers.
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
- Each file has one H1, then an H2 per source page followed by an `Official page: <url>` line, so retrieved fragments carry their source.
- The header blockquote records the source repo commit and the license. Angular docs (angular/angular, `adev/src/content/`) are MIT, Google LLC. The TypeScript docs (microsoft/TypeScript-Website, `packages/documentation/copy/en/`) are **CC BY 4.0**, which requires keeping the attribution, license link, and the "Changes" note.
- TypeScript "twoslash" code blocks are rendered as the site shows them: setup code above `// ---cut---` and `// @directive` / `^?` lines are removed, examples with `// @errors` get a first-line comment saying they intentionally fail to compile, and Playground links are reduced to their text.

## Known retrieval weaknesses

- The docs are English and users ask in Spanish. The multilingual embedding model fixed the worst misses (e.g. `takeUntilDestroyed`, required inputs), but some Spanish questions still retrieve the wrong section.
- `TOP_K=2` fragments of 500 chars is often too little context: the model either says it can't answer or fills gaps with wrong details (e.g. invented `@Input` syntax). Raising `TOP_K` is the next thing to try.
