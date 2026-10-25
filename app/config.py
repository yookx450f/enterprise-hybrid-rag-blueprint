"""
環境変数設定読み込みモジュール
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Ollama, ChromaDB 接続用環境設定"""

    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    LLM_MODEL: str = "gemma4:31b"
    EMBED_MODEL: str = "bge-m3"
    CHROMA_HOST: str = "chromadb"
    CHROMA_PORT: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
