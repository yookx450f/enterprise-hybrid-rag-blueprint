"""
環境変数設定読み込みモジュール
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Ollama, ChromaDB 接続用環境設定"""

    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_HOST_LOCAL: str = "http://host.docker.internal:11434"
    OLLAMA_HOST_M1MAX64GB: str = "http://100.126.109.125:11434"
    OLLAMA_HOST_RTX3090: str = "http://100.87.2.91:11434"
    LLM_MODEL: str = "gemma4:12b"
    EMBED_MODEL: str = "bge-m3"
    CHROMA_HOST: str = "chromadb"
    CHROMA_PORT: int = 8000
    EMBED_TIMEOUT: float = 60.0       # ベクトル化タイムアウト（秒）
    LLM_TIMEOUT: float = 600.0       # LLM推論タイムアウト（秒）

    class Config:
        env_file = ".env"


settings = Settings()


# ─── LLMホストアリアスマップ ─────────────────────────────────

OLLAMA_HOSTS: dict[str, str] = {
    "local": settings.OLLAMA_HOST_LOCAL,
    "m1max64gb": settings.OLLAMA_HOST_M1MAX64GB,
    "rtx3090": settings.OLLAMA_HOST_RTX3090,
}


def resolve_ollama_url(host_alias: str | None) -> str:
    """
    ホストアリアス名からOllamaのベースURLを解決する。
    アリアスが未指定またはマップにない場合はデフォルトURLを返す。
    """
    if host_alias and host_alias in OLLAMA_HOSTS:
        return OLLAMA_HOSTS[host_alias]
    return settings.OLLAMA_BASE_URL


def get_ollama_hosts() -> dict[str, str]:
    """利用可能なホストアリアス一覧を返す。"""
    return dict(OLLAMA_HOSTS)
