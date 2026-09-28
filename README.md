# DevTutor Bot API

A FastAPI backend for **DevTutor Bot**, a tutor that answers Angular and TypeScript questions in Spanish. It uses Retrieval-Augmented Generation (RAG): each question is matched against a local knowledge base built from the official Angular and TypeScript documentation, and the most relevant passages are sent to a small LLM, which answers from them.

<img width="1536" height="542" alt="Captura de pantalla 2026-09-28 a la(s) 1 10 12 a m" src="https://github.com/user-attachments/assets/4e3fcbee-86aa-486c-86e3-3646e111e702" />

It's designed to be called by a separate React frontend.

## How it works

1. **At startup**, every Markdown file in `docs_angular/` is split into ~500-character chunks, embedded with the multilingual [`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) through the Hugging Face Inference API, and kept in memory as a NumPy array. The multilingual model lets Spanish questions match the English docs.
2. **On each question**, the question is embedded the same way, the 2 most similar chunks (by cosine similarity) are retrieved, and they're passed, with the question, to [`Qwen/Qwen3-4B-Instruct-2507`](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) through [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers). The prompt tells the model to answer only from the provided docs.
3. **The response** includes the answer and the chunks it was based on.

Built with FastAPI, `huggingface_hub`, and NumPy, with no RAG framework: each step is a small function in `src/server.py`. `langchain-text-splitters` is used only to split the docs. By default both models run remotely on Hugging Face, so the app needs no GPU or PyTorch and uses about 80 MB of RAM.

The API can also run in a **local mode** that answers with a small model fine-tuned with LoRA for this bot; see [Fine-tuning](#fine-tuning-local-only).

## Project structure

| Path | What it is |
|---|---|
| `src/server.py` | The whole API: knowledge base loading, embeddings, retrieval, generation, and the endpoints |
| `docs_angular/` | The knowledge base (Markdown) |
| `scripts/generate_dataset.py` | Builds the fine-tuning dataset with a teacher LLM |
| `data/devtutor_sft.jsonl` | The fine-tuning dataset (249 examples) |
| `notebooks/fine_tuning.ipynb` | Colab notebook that trains the LoRA adapter |
| `models/` | Trained adapters, downloaded from Colab (git-ignored) |

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A [Hugging Face access token](https://huggingface.co/settings/tokens) with permission to make calls to Inference Providers (a read token is enough). It's used for both the embeddings and the LLM. Usage beyond Hugging Face's monthly free credits is billed to your account; when the credits run out, every question fails until they reset.
- Only for fine-tuning: a [Groq API key](https://console.groq.com/keys) (free) to regenerate the dataset, a Google account for Colab, and a machine with a few GB of free RAM to run the fine-tuned model (an Apple Silicon Mac or an NVIDIA GPU makes it faster).

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

The API runs at http://127.0.0.1:8000, with interactive docs at http://127.0.0.1:8000/docs. Startup takes a few seconds while the docs are embedded through the Hugging Face API; in the default mode the server refuses to start without `HF_API_KEY`.

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

### `GET /api/info`

Describes how the bot works, read from the running configuration: the mode (`remote` or `local_finetuned`), the RAG pipeline (knowledge base files and fragment count, chunking, embedding model and dimensions, similarity, `top_k`, prompt template), the LLM (model, where it runs, decoding settings, and in local mode its parameter count), and the fine-tuning setup (LoRA `r`/`alpha`/dropout/target modules, plus the training hyperparameters, loss and dataset size from the adapter's `training_info.json`, when the adapter is available).

```bash
curl http://127.0.0.1:8000/api/info
```

### `GET /api/health`

Returns `{"status": "online", "service": "DevTutor Bot API"}`. It only checks that the server is up, not the LLM provider.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `HF_API_KEY` | Yes (default mode) | Hugging Face access token used for the embeddings and the LLM. Not needed with `USE_FINETUNED=1`. |
| `USE_FINETUNED` | No | `1` runs the embeddings and the fine-tuned model locally instead of calling Hugging Face (see [Fine-tuning](#fine-tuning-local-only)). |
| `FINETUNED_ADAPTER` | No | Path or Hub repo id of the LoRA adapter for `USE_FINETUNED=1`. Defaults to `./models/devtutor-lora`. |
| `GROQ_API_KEY` | No | Only for `scripts/generate_dataset.py`: Groq key for the teacher model. |
| `TEACHER_MODEL` | No | Only for `scripts/generate_dataset.py`: Groq model that writes the dataset. Defaults to `qwen/qwen3.8-27b`. |
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

The image (~490 MB) includes the `docs_angular/` knowledge base, runs as a non-root user, listens on `$PORT` if set (otherwise 8000), and has a health check on `/api/health`. `.env` is never copied into the image, and neither are the fine-tuning files or dependencies.

The container runs the code as it was when the image was built. After changing the code or the docs, rebuild the image and restart the container; for development, `uv run fastapi dev` (which reloads on every change) is more convenient.

## Deploy to Render (free)

`render.yaml` defines a Docker web service on Render's free plan (0.1 CPU, 512 MB RAM; the app uses ~80 MB). Render always runs the default (remote) mode.

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

## Fine-tuning (local only)

Besides the default setup (RAG + remote Qwen), the API can answer with a small model fine-tuned with LoRA to behave like DevTutor Bot: answering in Spanish, concisely, only from the retrieved docs, and saying so when they don't cover the question. Fine-tuning teaches that *style*; the facts still come from RAG.

The fine-tuned model runs on your machine, not on Render (a 1.5B model needs a few GB of RAM).

1. **The dataset** (`data/devtutor_sft.jsonl`, already generated) has 249 examples: 224 questions about the docs and 25 off-topic questions the bot should decline (React, Tailwind, NgRx…). Each example is the exact prompt the API sends (retrieved docs + question) and the answer we want back, written by a larger teacher model (`qwen/qwen3.8-27b` on Groq): in Spanish, 2 to 6 sentences, a short code example when it helps, and never inventing anything outside the docs. A few answers were cleaned up by hand.

   To regenerate it (for example, after changing the docs or the prompt), add `GROQ_API_KEY` to `src/.env` ([free key](https://console.groq.com/keys)) and run the command below. It takes about 40 minutes because of Groq's free-tier rate limits, and it overwrites the manual cleanup.

   ```bash
   uv sync --group finetuned
   uv run python -m scripts.generate_dataset   # writes data/devtutor_sft.jsonl
   ```

   The notebook downloads the dataset from the `main` branch on GitHub, so push it after regenerating (or upload it by hand in Colab).

2. **Train in Colab.** Open [`notebooks/fine_tuning.ipynb`](notebooks/fine_tuning.ipynb) in Google Colab, choose **Runtime > Change runtime type > T4 GPU**, and run all cells:

   [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/mcastig/api-dev-tutor-bot/blob/main/notebooks/fine_tuning.ipynb)

   It fine-tunes `Qwen/Qwen2.5-1.5B-Instruct` with LoRA (`peft` + `trl`'s `SFTTrainer`), compares answers before and after, and downloads `devtutor-lora.zip`. The zip also contains `training_info.json` (hyperparameters, loss, dataset size), which `GET /api/info` shows.

3. **Run the API with it.** Unzip the adapter into `models/devtutor-lora/` (git-ignored) and start the server in local mode:

   ```bash
   USE_FINETUNED=1 uv run fastapi dev
   ```

   The embeddings also run locally in this mode, so it needs no Hugging Face credits. The first start downloads the base model (~3 GB). Answers take about 4–20 seconds each on an Apple Silicon Mac. To use an adapter pushed to the Hugging Face Hub instead, set `FINETUNED_ADAPTER=your-user/devtutor-lora`.

   Check that it's active with `GET /api/info`: `mode` is `local_finetuned`, and `fine_tuning` shows the LoRA and training details.

The `finetuned` dependency group (PyTorch, transformers, peft, trl, sentence-transformers, accelerate) is only installed with `uv sync --group finetuned`; a plain `uv sync` removes it again. The Docker image never includes it.

## Known limitations

- **Retrieval isn't perfect across languages.** The multilingual embeddings help Spanish questions find the English docs, but some questions still miss the right section.
- **Only 2 chunks are retrieved per question**, which is sometimes too little context. The model can then answer with wrong details, such as incorrect syntax.
- **Doc changes need a restart**; the index is built once at startup and kept in memory.
- **The default mode depends on Hugging Face**: the docs are embedded through the API on every start, so the server can't start if the API is unreachable or the account's credits have run out.
- **The fine-tuned model only runs locally.** It's also smaller (1.5B) than the remote model (4B): fine-tuning improves its style and its refusals, but it can still get details wrong.
