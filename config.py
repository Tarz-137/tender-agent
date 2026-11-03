import os

# Increment when replacing an Ollama model under the same tag.
EVALUATION_CACHE_VERSION = os.getenv("EVALUATION_CACHE_VERSION", "1")

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
)

OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "nomic-embed-text:latest",
)

OLLAMA_CHAT_MODEL = os.getenv(
    "OLLAMA_CHAT_MODEL",
    "qwen3.5:9b-q4_K_M"
)
