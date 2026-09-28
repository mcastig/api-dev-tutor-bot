"""Generate the fine-tuning dataset for DevTutor Bot.

Each example is exactly what the API sends the LLM (the Spanish PROMPT
filled with the retrieved fragments and a question) plus the answer we want
back. A large "teacher" model writes the questions and answers, so the small
fine-tuned model learns the DevTutor style: Spanish, concise, answering only
from the context, and saying so when the context doesn't cover the question.

The teacher runs on Groq (free tier, OpenAI-compatible API) and the
embeddings run locally with sentence-transformers (same model as the API), so
this uses no Hugging Face credits.

Run from the repo root (needs GROQ_API_KEY in src/.env):
    uv sync --group finetuned
    uv run python -m scripts.generate_dataset

Output: data/devtutor_sft.jsonl, one example per line in TRL's conversational
prompt-completion format ({"prompt": [...], "completion": [...]}) plus
"pregunta" and "tipo" for inspection.
"""

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from sentence_transformers import SentenceTransformer

from src.server import EMBEDDING_MODEL, PROMPT, TOP_K, load_fragments

# Large model on Groq that writes the training data (distillation). Only used
# here, never by the API. Override with the TEACHER_MODEL env var.
TEACHER_MODEL = os.getenv("TEACHER_MODEL", "qwen/qwen3.8-27b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Parallel teacher calls; kept low to stay under Groq's free-tier rate limits.
MAX_WORKERS = 2

# Number of doc fragments turned into questions. Off-topic questions are
# added on top of these.
NUM_QUESTIONS = 230

# Fragments shorter than this are mostly headings or link lists.
MIN_FRAGMENT_CHARS = 250

OUTPUT_PATH = Path("data/devtutor_sft.jsonl")

QUESTION_PROMPT = """Este es un fragmento de la documentación oficial de Angular o TypeScript:

{fragment}

Escribe UNA pregunta que un estudiante hispanohablante le haría a un tutor y que este fragmento responda.
Varía el estilo: a veces conceptual ("¿qué es...?", "¿para qué sirve...?"), a veces práctica ("¿cómo hago...?"), a veces de comparación o de error común.
Escribe como escribe un estudiante real: breve y natural, sin citar el fragmento. Mantén en inglés los nombres de APIs y código.
Devuelve solo la pregunta."""

# The teacher sees the same context the API would retrieve, plus explicit
# style rules; the student is later trained on the plain PROMPT only, so it
# has to learn the style instead of reading it.
ANSWER_PROMPT = """Eres DevTutor Bot, un Desarrollador Frontend Senior experto en Angular y TypeScript que da tutorías en español.

Reglas:
- Responde SOLO con información del contexto. No agregues APIs, opciones ni sintaxis que no aparezcan en él.
- Si el contexto no contiene la respuesta, dilo en una o dos frases (por ejemplo: "La documentación que tengo no cubre eso.") y, si aplica, menciona qué temas sí cubre el contexto. No inventes.
- El estudiante no ve el contexto: nunca digas "el contexto" ni "el fragmento"; si necesitas referirte a él, di "la documentación".
- Ve directo al punto: 2 a 6 frases. Sin saludos, sin encabezados, sin emojis.
- Si ayuda, incluye un ejemplo de código corto (```ts o ```html) basado en el contexto.
- Usa `backticks` para nombres de APIs, y mantenlos en inglés.

Contexto:
{context}

Pregunta:
{question}
Respuesta:"""

# Questions the knowledge base doesn't cover, so the model learns to say so
# instead of inventing an answer.
OFF_TOPIC_QUESTIONS = [
    "¿Cómo uso useEffect en React?",
    "¿Qué diferencia hay entre Vue 3 y Angular?",
    "¿Cómo configuro Tailwind CSS en mi proyecto?",
    "¿Cómo despliego mi app de Angular en Firebase Hosting?",
    "¿Cómo hago pruebas unitarias con Jasmine y Karma?",
    "¿Cómo configuro rutas con parámetros en Angular Router?",
    "¿Qué es NgRx y cómo creo un store?",
    "¿Cómo uso Angular Material para hacer una tabla?",
    "¿Cómo hago server-side rendering con Angular SSR?",
    "¿Cómo creo un formulario reactivo con FormBuilder?",
    "¿Cómo conecto mi app a una base de datos MySQL?",
    "¿Cómo escribo un decorador de clase personalizado en TypeScript?",
    "¿Cómo configuro webpack manualmente?",
    "¿Qué es Next.js?",
    "¿Cómo hago animaciones con el módulo de animaciones de Angular?",
    "¿Cómo instalo Node.js en Windows?",
    "¿Cómo internacionalizo mi app con i18n?",
    "¿Cómo uso Docker para una app de Angular?",
    "¿Cuál es la diferencia entre let y var en JavaScript?",
    "¿Cómo creo una PWA con service workers en Angular?",
    "¿Cómo hago una petición GraphQL con Apollo?",
    "¿Cómo configuro ESLint en un proyecto de Angular?",
    "¿Qué es Deno?",
    "¿Cómo manejo la autenticación con JWT en el backend?",
    "¿Cómo escribo pruebas end-to-end con Cypress?",
]


def ask_teacher(client: InferenceClient, prompt: str) -> str:
  """Call the teacher, waiting and retrying when Groq rate-limits us."""
  for attempt in range(8):
    try:
      response = client.chat_completion(
          messages=[{"role": "user", "content": prompt}],
          model=TEACHER_MODEL,
          temperature=0.7,
          max_tokens=600,
      )
      return response.choices[0].message.content.strip()
    except HfHubHTTPError as e:
      if e.response is None or e.response.status_code != 429:
        raise
      wait = float(e.response.headers.get("retry-after") or 2 ** attempt)
      time.sleep(min(wait, 60))
  raise RuntimeError("Teacher still rate-limited after 8 attempts")


class Retriever:
  """Same retrieval as the API (normalized vectors, top-k dot product), with
  the embeddings computed locally instead of through Hugging Face."""

  def __init__(self, fragments: list[str]):
    self.fragments = fragments
    self.model = SentenceTransformer(EMBEDDING_MODEL)
    self.vectors = self.model.encode(fragments, normalize_embeddings=True)

  def search(self, question: str) -> list[str]:
    question_vector = self.model.encode(
        [question], normalize_embeddings=True, show_progress_bar=False
    )[0]
    best = np.argsort(self.vectors @ question_vector)[::-1][:TOP_K]
    return [self.fragments[i] for i in best]


def to_example(context: list[str], question: str, answer: str, kind: str):
  """Build one example in TRL's conversational prompt-completion format."""
  prompt = PROMPT.format(context="\n\n".join(context), question=question)
  return {
      "prompt": [{"role": "user", "content": prompt}],
      "completion": [{"role": "assistant", "content": answer}],
      "pregunta": question,
      "tipo": kind,
  }


def main():
  teacher = InferenceClient(
      base_url=GROQ_BASE_URL, api_key=os.environ["GROQ_API_KEY"]
  )

  # Same retrieval as the API, so training contexts look like real ones.
  retriever = Retriever(load_fragments())

  rng = random.Random(42)
  candidates = [f for f in retriever.fragments if len(f) >= MIN_FRAGMENT_CHARS]
  sources = rng.sample(candidates, min(NUM_QUESTIONS, len(candidates)))

  def from_fragment(fragment: str):
    question = ask_teacher(teacher, QUESTION_PROMPT.format(fragment=fragment))
    context = retriever.search(question)
    # Retrieval sometimes misses the fragment the question came from; keep
    # it in the context so the teacher's answer stays grounded.
    if fragment not in context:
      context = [fragment, context[0]]
    answer = ask_teacher(
        teacher, ANSWER_PROMPT.format(context="\n\n".join(context), question=question)
    )
    return to_example(context, question, answer, "docs")

  def off_topic(question: str):
    context = retriever.search(question)
    answer = ask_teacher(
        teacher, ANSWER_PROMPT.format(context="\n\n".join(context), question=question)
    )
    return to_example(context, question, answer, "fuera_de_tema")

  # Written as each example finishes, so a run cut short (e.g. by Groq's
  # daily limit) keeps what it has. The notebook shuffles when splitting.
  OUTPUT_PATH.parent.mkdir(exist_ok=True)
  written = 0
  with OUTPUT_PATH.open("w", encoding="utf-8") as f, ThreadPoolExecutor(
      max_workers=MAX_WORKERS
  ) as pool:
    jobs = [pool.submit(from_fragment, fragment) for fragment in sources]
    jobs += [pool.submit(off_topic, question) for question in OFF_TOPIC_QUESTIONS]
    for i, job in enumerate(jobs, 1):
      try:
        f.write(json.dumps(job.result(), ensure_ascii=False) + "\n")
        f.flush()
        written += 1
      except Exception as e:  # skip the odd failed call, keep the rest
        print(f"[{i}/{len(jobs)}] skipped: {e}", flush=True)
      if i % 10 == 0:
        print(f"[{i}/{len(jobs)}] done", flush=True)
  print(f"Wrote {written} examples to {OUTPUT_PATH}")

if __name__ == "__main__":
  main()
