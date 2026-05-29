import os
import pathlib

from google import genai


def _load_env():
    env_path = pathlib.Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


_load_env()

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    result = _client.models.embed_content(
        model="gemini-embedding-001",
        contents=texts,
        config={"task_type": task_type, "output_dimensionality": 768},
    )
    return [e.values for e in result.embeddings]


def embed_query(text: str) -> list[float]:
    result = _client.models.embed_content(
        model="gemini-embedding-001",
        contents=[text],
        config={"task_type": "RETRIEVAL_QUERY", "output_dimensionality": 768},
    )
    return result.embeddings[0].values
