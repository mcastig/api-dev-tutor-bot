"""DevTutor Bot API.

A FastAPI service that answers Angular/TypeScript questions using
Retrieval-Augmented Generation (RAG): relevant snippets are retrieved from
local Markdown docs and passed as context to a small LLM.

The pipeline is written out step by step (no RAG framework):
  1. Load the knowledge base and split it into fragments.
  2. Embed every fragment once, at startup.
  3. For each question, find the most similar fragments (dot product).
  4. Build a prompt with those fragments and ask the LLM.

Models run in one of two modes:
  - Remote (default, used on Render): embeddings and the LLM are Hugging Face
    API calls, so the app needs no PyTorch and fits in 512 MB.
  - Local (USE_FINETUNED=1): embeddings and our LoRA fine-tuned model run on
    this machine. Needs `uv sync --group finetuned` and a trained adapter
    (see notebooks/fine_tuning.ipynb). No API key or credits needed.

Environment variables (loaded from src/.env):
    HF_API_KEY: Required in remote mode. Hugging Face access token (read
        access is enough) used for the embeddings and the LLM.
    USE_FINETUNED: Optional. "1" switches to local mode.
    FINETUNED_ADAPTER: Optional. Path (or Hub repo id) of the LoRA adapter
        used in local mode. Defaults to ./models/devtutor-lora.
    CORS_ORIGINS: Optional. Comma-separated frontend origins allowed to call
        the API, e.g. "https://app.example.com". Defaults to local dev servers.
"""

import json
import logging
import os
import threading
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

# LoRA adapter trained on top of Qwen/Qwen2.5-1.5B-Instruct (the base model is
# read from the adapter's config). Only used in local mode.
DEFAULT_FINETUNED_ADAPTER = "./models/devtutor-lora"

# Multilingual embedding model, so Spanish questions match the English docs.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Fragment size and overlap, in characters.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Chunks embedded per API request at startup (each call sends all of its
# texts in one request, so large corpora are split into batches).
EMBEDDING_BATCH_SIZE = 64

# Fragments passed to the LLM per question; kept small to keep the prompt small.
TOP_K = 2

# Longest answer, in tokens; caps latency (and cost in remote mode).
MAX_ANSWER_TOKENS = 512

# Low temperature keeps remote answers close to the provided docs (local mode
# uses greedy decoding, the equivalent of temperature 0).
LLM_TEMPERATURE = 0.1

# Knowledge base; the path is relative to the current working directory.
DOCS_DIR = Path("./docs_angular")

# Also used to build the fine-tuning dataset (scripts/generate_dataset.py),
# so the fine-tuned model is trained on exactly this format.
PROMPT = """Eres DevTutor Bot, un Desarrollador Frontend Senior experto en Angular y TypeScript.
Usa estrictamente la documentación provista para responder. Si no lo sabes, dilo.

Contexto:
{context}

Pregunta:
{question}
Respuesta:"""


# ---- 1. Knowledge base -------------------------------------------------------


def list_docs() -> list[Path]:
  """The knowledge base's Markdown files, in a stable order."""
  return sorted(DOCS_DIR.glob("**/*.md"))


def load_fragments() -> list[str]:
  """Read every .md file in DOCS_DIR and split it into ~500-char fragments.

  A small overlap keeps sentences cut at a boundary intact in at least one
  fragment.
  """
  paths = list_docs()
  if not paths:
    raise RuntimeError(f"No .md files found in {DOCS_DIR.resolve()}")

  splitter = RecursiveCharacterTextSplitter(
      chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
  )
  fragments = []
  for path in paths:
    fragments.extend(splitter.split_text(path.read_text(encoding="utf-8")))
  return fragments


# ---- 2. Models (embeddings + LLM) --------------------------------------------
#
# Both classes expose the same methods:
#   embed(texts) -> one L2-normalized row per text. Normalizing makes the dot
#       product in search_fragments() equal to cosine similarity, so fragment
#       length doesn't skew the ranking.
#   generate(prompt) -> the LLM's answer.
#   describe() -> mode, embeddings and LLM details for /api/info.


def describe_adapter(adapter: str) -> dict | None:
  """LoRA and training details of a fine-tuned adapter, or None if missing.

  Reads adapter_config.json (written by peft) and, if present,
  training_info.json (written by notebooks/fine_tuning.ipynb) from a local
  directory or a Hub repo. Needs no PyTorch.
  """

  def read_json(name: str) -> dict | None:
    path = Path(adapter) / name
    try:
      if not path.is_file():
        from huggingface_hub import hf_hub_download

        path = Path(hf_hub_download(adapter, name))
      return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # not downloaded/trained yet, or not a Hub repo
      return None

  config = read_json("adapter_config.json")
  if config is None:
    return None
  return {
      "adapter": adapter,
      "base_model": config.get("base_model_name_or_path"),
      "method": config.get("peft_type"),
      "lora": {
          "r": config.get("r"),
          "lora_alpha": config.get("lora_alpha"),
          "lora_dropout": config.get("lora_dropout"),
          "target_modules": sorted(config.get("target_modules") or []),
      },
      "training": read_json("training_info.json"),
  }


class RemoteModels:
  """Embeddings and LLM served by Hugging Face (uses HF credits)."""

  def __init__(self, api_key: str):
    self.embeddings_client = InferenceClient(
        provider="hf-inference", api_key=api_key
    )
    # "auto" lets Hugging Face pick an available provider for the LLM.
    self.llm_client = InferenceClient(provider="auto", api_key=api_key)

  def describe(self) -> dict:
    return {
        "mode": "remote",
        "embeddings_runtime": "Hugging Face Inference API (hf-inference)",
        "llm": {
            "model": LLM_MODEL,
            "runtime": "Hugging Face Inference Providers (provider=auto)",
            "fine_tuned": False,
            "temperature": LLM_TEMPERATURE,
            "max_new_tokens": MAX_ANSWER_TOKENS,
        },
    }

  def embed(self, texts: list[str]) -> np.ndarray:
    batches = [
        self.embeddings_client.feature_extraction(
            texts[start : start + EMBEDDING_BATCH_SIZE], model=EMBEDDING_MODEL
        )
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE)
    ]
    vectors = np.vstack(batches).astype(np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

  def generate(self, prompt: str) -> str:
    response = self.llm_client.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        max_tokens=MAX_ANSWER_TOKENS,
    )
    return response.choices[0].message.content


class LocalModels:
  """Embeddings and the LoRA fine-tuned LLM running on this machine."""

  def __init__(self, adapter: str):
    # Imported here so remote mode (and the Docker image) never needs them.
    import torch
    from peft import AutoPeftModelForCausalLM
    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer

    self.embedder = SentenceTransformer(EMBEDDING_MODEL)

    # Apple GPU (MPS) or NVIDIA (CUDA) if available, else CPU.
    if torch.backends.mps.is_available():
      device, dtype = "mps", torch.float16
    elif torch.cuda.is_available():
      device, dtype = "cuda", torch.float16
    else:
      device, dtype = "cpu", torch.float32
    # Loads the base model named in the adapter's config, then applies the
    # adapter; merging makes generation as fast as a plain model.
    model = AutoPeftModelForCausalLM.from_pretrained(adapter, dtype=dtype)
    # Count before merging, which folds the LoRA weights into the base ones.
    lora_params = sum(
        p.numel() for name, p in model.named_parameters() if "lora_" in name
    )
    self.model = model.merge_and_unload().to(device).eval()
    self.tokenizer = AutoTokenizer.from_pretrained(adapter)
    self.adapter = adapter
    self.base_model = model.peft_config["default"].base_model_name_or_path
    self.device, self.dtype = device, str(dtype).removeprefix("torch.")
    self.params = {
        "base": self.model.num_parameters(),
        "lora": lora_params,
        "lora_percent": round(100 * lora_params / self.model.num_parameters(), 2),
    }
    # generate() isn't safe to run concurrently on one model, and FastAPI runs
    # sync endpoints in a thread pool.
    self.lock = threading.Lock()
    logger.info("Loaded fine-tuned model from %s on %s", adapter, device)

  def describe(self) -> dict:
    return {
        "mode": "local_finetuned",
        "embeddings_runtime": "local (sentence-transformers)",
        "llm": {
            "model": self.base_model,
            "adapter": self.adapter,
            "runtime": f"local transformers on {self.device} ({self.dtype})",
            "fine_tuned": True,
            "parameters": self.params,
            "decoding": "greedy (do_sample=False)",
            "max_new_tokens": MAX_ANSWER_TOKENS,
        },
    }

  def embed(self, texts: list[str]) -> np.ndarray:
    return self.embedder.encode(
        texts, normalize_embeddings=True, show_progress_bar=False
    )

  def generate(self, prompt: str) -> str:
    inputs = self.tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}],
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    ).to(self.model.device)
    with self.lock:
      # Greedy decoding: deterministic and close to the training answers.
      output = self.model.generate(
          **inputs, max_new_tokens=MAX_ANSWER_TOKENS, do_sample=False
      )
    new_tokens = output[0][inputs["input_ids"].shape[1] :]
    return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


# ---- 3. Retrieval ------------------------------------------------------------


def search_fragments(question: str, state) -> list[str]:
  """Return the TOP_K fragments most similar to the question."""
  question_vector = state.models.embed([question])[0]
  similarities = state.fragment_vectors @ question_vector
  best = np.argsort(similarities)[::-1][:TOP_K]
  return [state.fragments[i] for i in best]


# ---- 4. Generation -----------------------------------------------------------


def assistant(question: str, state) -> dict:
  """RAG: retrieve fragments for the question, then answer from them."""
  fragments = search_fragments(question, state)
  prompt = PROMPT.format(context="\n\n".join(fragments), question=question)
  return {
      "respuesta": state.models.generate(prompt),
      "fuentes": fragments,
  }


# ---- API ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Load the models and embed the knowledge base before serving requests.

  Restart the server to pick up changes to the docs.
  """
  if os.getenv("USE_FINETUNED") == "1":
    app.state.models = LocalModels(
        os.getenv("FINETUNED_ADAPTER") or DEFAULT_FINETUNED_ADAPTER
    )
  else:
    # Both the embeddings and the LLM need the key, so refuse to start
    # without it rather than fail on every request.
    api_key = os.getenv("HF_API_KEY")
    if not api_key:
      raise RuntimeError("HF_API_KEY is not set (see src/.env.example)")
    app.state.models = RemoteModels(api_key)

  app.state.fragments = load_fragments()
  app.state.fragment_vectors = app.state.models.embed(app.state.fragments)
  logger.info("Embedded %d fragments", len(app.state.fragments))
  yield


app = FastAPI(title="DevTutor Bot API", version="1.0", lifespan=lifespan)

# Origins must match exactly (scheme + host + port, no trailing slash).
# Defaults cover the production frontend on Vercel and the Vite (5173) and
# Create React App (3000) dev servers.
DEFAULT_CORS_ORIGINS = ",".join([
    "https://frontend-dev-tutor-bot.vercel.app",
    "http://localhost:5173",
    "http://localhost:3000",
])
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


@app.get("/api/info")
def info(request: Request):
  """Describe the RAG pipeline, the models and the fine-tuning setup.

  Everything comes from the running configuration, so it always matches
  what /api/ask actually does. Never includes API keys.
  """
  state = request.app.state
  models = state.models.describe()
  adapter = os.getenv("FINETUNED_ADAPTER") or DEFAULT_FINETUNED_ADAPTER
  adapter_info = describe_adapter(adapter)
  return {
      "service": "DevTutor Bot API",
      "mode": models["mode"],
      "rag": {
          "knowledge_base": {
              "directory": str(DOCS_DIR),
              "files": [path.name for path in list_docs()],
              "fragments": len(state.fragments),
          },
          "chunking": {
              "splitter": "RecursiveCharacterTextSplitter",
              "chunk_size": CHUNK_SIZE,
              "chunk_overlap": CHUNK_OVERLAP,
          },
          "embeddings": {
              "model": EMBEDDING_MODEL,
              "dimensions": int(state.fragment_vectors.shape[1]),
              "runtime": models["embeddings_runtime"],
              "normalized": True,
          },
          "retrieval": {
              "vector_store": "in-memory NumPy matrix",
              "similarity": "cosine (dot product of L2-normalized vectors)",
              "top_k": TOP_K,
          },
          "prompt_template": PROMPT,
      },
      "llm": models["llm"],
      "fine_tuning": {
          # Served only in local mode (USE_FINETUNED=1); the adapter's
          # details are shown whenever its files are available.
          "enabled": models["mode"] == "local_finetuned",
          "notebook": "notebooks/fine_tuning.ipynb",
          "dataset_script": "scripts/generate_dataset.py",
          **(adapter_info or {"adapter": None}),
      },
  }


@app.get("/api/health")
def health_check():
  """Liveness probe; does not check the LLM provider or the embeddings."""
  return {"status": "online", "service": "DevTutor Bot API"}
