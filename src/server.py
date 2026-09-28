"""DevTutor Bot API.

A FastAPI service that answers Angular/TypeScript questions using
Retrieval-Augmented Generation (RAG): relevant snippets are retrieved from
local Markdown docs and passed as context to a small LLM served by Hugging
Face Inference Providers.

The pipeline is written out step by step (no RAG framework):
  1. Load the knowledge base and split it into fragments.
  2. Embed every fragment once, at startup.
  3. For each question, find the most similar fragments (dot product).
  4. Build a prompt with those fragments and ask the LLM.

Environment variables (loaded from src/.env):
    HF_API_KEY: Required. Hugging Face access token (read access is enough)
        used for the embeddings and the LLM.
    CORS_ORIGINS: Optional. Comma-separated frontend origins allowed to call
        the API, e.g. "https://app.example.com". Defaults to local dev servers.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub import InferenceClient
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel

# Load env vars from the .env next to this file, so it works regardless of
# the directory the server is started from.
load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

# Small (4B) instruction-tuned chat model with good Spanish support, served
# by Hugging Face Inference Providers. The "Instruct" variant answers directly
# (no <think> reasoning blocks in the output).
LLM_MODEL = "Qwen/Qwen3-4B-Instruct-2507"

# Multilingual embedding model, so Spanish questions match the English docs.
# Computed remotely by Hugging Face (no local PyTorch), which keeps the app
# small enough for 512 MB free hosting tiers.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Chunks embedded per API request at startup (each call sends all of its
# texts in one request, so large corpora are split into batches).
EMBEDDING_BATCH_SIZE = 64

# Fragments passed to the LLM per question; kept small to keep the prompt small.
TOP_K = 2

# Knowledge base; the path is relative to the current working directory.
DOCS_DIR = Path("./docs_angular")

PROMPT = """Eres DevTutor Bot, un Desarrollador Frontend Senior experto en Angular y TypeScript.
Usa estrictamente la documentación provista para responder. Si no lo sabes, dilo.

Contexto:
{context}

Pregunta:
{question}
Respuesta:"""


# ---- 1. Knowledge base -------------------------------------------------------


def load_fragments() -> list[str]:
  """Read every .md file in DOCS_DIR and split it into ~500-char fragments.

  A small overlap keeps sentences cut at a boundary intact in at least one
  fragment.
  """
  paths = sorted(DOCS_DIR.glob("**/*.md"))
  if not paths:
    raise RuntimeError(f"No .md files found in {DOCS_DIR.resolve()}")

  splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
  fragments = []
  for path in paths:
    fragments.extend(splitter.split_text(path.read_text(encoding="utf-8")))
  return fragments


# ---- 2. Embeddings -----------------------------------------------------------


def embed(client: InferenceClient, texts: list[str]) -> np.ndarray:
  """Embed texts remotely, returning one L2-normalized row per text.

  Normalizing makes the dot product in search_fragments() equal to cosine
  similarity, so fragment length doesn't skew the ranking.
  """
  batches = [
      client.feature_extraction(
          texts[start : start + EMBEDDING_BATCH_SIZE], model=EMBEDDING_MODEL
      )
      for start in range(0, len(texts), EMBEDDING_BATCH_SIZE)
  ]
  vectors = np.vstack(batches).astype(np.float32)
  return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


# ---- 3. Retrieval ------------------------------------------------------------


def search_fragments(question: str, state) -> list[str]:
  """Return the TOP_K fragments most similar to the question."""
  question_vector = embed(state.embeddings_client, [question])[0]
  similarities = state.fragment_vectors @ question_vector
  best = np.argsort(similarities)[::-1][:TOP_K]
  return [state.fragments[i] for i in best]


# ---- 4. Generation -----------------------------------------------------------


def assistant(question: str, state) -> dict:
  """RAG: retrieve fragments for the question, then answer from them."""
  fragments = search_fragments(question, state)
  prompt = PROMPT.format(context="\n\n".join(fragments), question=question)

  # Low temperature keeps answers close to the provided docs; max_tokens caps
  # answer length (and cost).
  response = state.llm_client.chat_completion(
      messages=[{"role": "user", "content": prompt}],
      model=LLM_MODEL,
      temperature=0.1,
      max_tokens=512,
  )
  return {
      "respuesta": response.choices[0].message.content,
      "fuentes": fragments,
  }


# ---- API ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Embed the knowledge base once before the server accepts requests.

  Restart the server to pick up changes to the docs.
  """
  # Both the embeddings and the LLM need the key, so refuse to start without
  # it rather than fail on every request.
  api_key = os.getenv("HF_API_KEY")
  if not api_key:
    raise RuntimeError("HF_API_KEY is not set (see src/.env.example)")

  app.state.embeddings_client = InferenceClient(
      provider="hf-inference", api_key=api_key
  )
  # "auto" lets Hugging Face pick an available provider for the LLM.
  app.state.llm_client = InferenceClient(provider="auto", api_key=api_key)

  app.state.fragments = load_fragments()
  app.state.fragment_vectors = embed(
      app.state.embeddings_client, app.state.fragments
  )
  logger.info("Embedded %d fragments", len(app.state.fragments))
  yield

app = FastAPI(title="DevTutor Bot API", version="1.0", lifespan=lifespan)

# Origins must match exactly (scheme + host + port, no trailing slash).
# Defaults cover the Vite (5173) and Create React App (3000) dev servers.
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"
CORS_ORIGINS = [
    origin.strip().rstrip("/")
    # `or`, not a getenv default, so an empty CORS_ORIGINS also uses the defaults.
    for origin in (os.getenv("CORS_ORIGINS") or DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

# Allow only the React frontend to call this API from the browser. The API
# uses no cookies or auth headers, so credentials stay disabled, and only the
# methods/headers the endpoints actually need are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class QueryRequest(BaseModel):
  """Request body for /api/ask."""

  pregunta: str  # The user's question.


@app.post("/api/ask")
def ask_tutor(body: QueryRequest, request: Request):
  """Answer a question using retrieved docs as context.

  Returns the LLM answer ("respuesta") and the raw text of the fragments it
  was given ("fuentes"), so the client can show sources.
  """
  try:
    return assistant(body.pregunta, request.app.state)
  except Exception:
    # Log the full traceback server-side, but return a generic message so
    # internal details (paths, provider errors, keys) never reach clients.
    logger.exception("Failed to answer question")
    raise HTTPException(
        status_code=500,
        detail="Ocurrió un error al procesar la pregunta. Inténtalo de nuevo.",
    )


@app.get("/api/health")
def health_check():
  """Liveness probe; does not check the LLM provider or the embeddings."""
  return {"status": "online", "service": "DevTutor Bot API"}
