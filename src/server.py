"""DevTutor Bot API.

A FastAPI service that answers Angular/TypeScript questions using
Retrieval-Augmented Generation (RAG): relevant snippets are retrieved from
local Markdown docs and passed as context to a small LLM served by Hugging
Face Inference Providers.

Environment variables (loaded from src/.env):
    HF_API_KEY: Required. Hugging Face access token used to call the LLM.
    CORS_ORIGINS: Optional. Comma-separated frontend origins allowed to call
        the API, e.g. "https://app.example.com". Defaults to local dev servers.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_classic.chains import RetrievalQA
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import (
    ChatHuggingFace,
    HuggingFaceEmbeddings,
    HuggingFaceEndpoint,
)
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

# {context} and {question} are filled in by RetrievalQA.
PROMPT = PromptTemplate.from_template(
    """Eres DevTutor Bot, un Desarrollador Frontend Senior experto en Angular y TypeScript.
Usa estrictamente la documentación provista para responder. Si no lo sabes, dilo.

Contexto:
{context}

Pregunta:
{question}
Respuesta:"""
)


def get_retriever():
  """Build a retriever over the Markdown files in ./docs_angular.

  Pipeline: load .md files -> split into chunks -> embed locally with
  MiniLM -> index in an in-memory FAISS store.

  This is expensive (loads the embedding model and embeds every doc), so it
  runs once at startup. Restart the server to pick up changes to the docs.

  Note: the path is relative to the current working directory, not this file.
  """
  loader = DirectoryLoader(
      "./docs_angular", glob="**/*.md", loader_cls=TextLoader
  )
  documents = loader.load()

  # ~500-char chunks with a small overlap so sentences cut at a boundary
  # still appear intact in at least one chunk.
  splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
  chunks = splitter.split_documents(documents)

  # Runs locally (downloaded on first use); no API key required.
  embeddings = HuggingFaceEmbeddings(
      model_name="sentence-transformers/all-MiniLM-L6-v2"
  )
  vector_store = FAISS.from_documents(chunks, embeddings)

  # Return the 2 most similar chunks per query to keep the prompt small.
  return vector_store.as_retriever(search_kwargs={"k": 2})


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Build the retriever once before the server starts accepting requests."""
  app.state.retriever = get_retriever()
  yield


app = FastAPI(title="DevTutor Bot API", version="1.0", lifespan=lifespan)

# Origins must match exactly (scheme + host + port, no trailing slash).
# Defaults cover the Vite (5173) and Create React App (3000) dev servers.
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"
CORS_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
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

  Returns the LLM answer ("respuesta") and the raw text of the chunks it was
  given ("fuentes"), so the client can show sources.
  """
  # Fail fast with a clear message instead of an opaque auth error from HF.
  if not os.getenv("HF_API_KEY"):
    raise HTTPException(
        status_code=500, detail="Falta configurar HF_API_KEY en el servidor"
    )

  try:
    # The LLM client and chain are cheap to create (no model download; it's a
    # remote API), so they're built per request with the current key.
    # Low temperature keeps answers close to the provided docs; max_new_tokens
    # caps answer length (and cost).
    llm = ChatHuggingFace(
        llm=HuggingFaceEndpoint(
            repo_id=LLM_MODEL,
            provider="auto",  # let Hugging Face pick an available provider
            huggingfacehub_api_token=os.getenv("HF_API_KEY"),
            temperature=0.1,
            max_new_tokens=512,
        )
    )

    # "stuff" = concatenate all retrieved chunks into a single prompt.
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=request.app.state.retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": PROMPT},
    )

    result = qa_chain.invoke({"query": body.pregunta})
    return {
        "respuesta": result["result"],
        "fuentes": [
            doc.page_content for doc in result["source_documents"]
        ],
    }
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
  """Liveness probe; does not check the LLM provider or the vector store."""
  return {"status": "online", "service": "DevTutor Bot API"}
