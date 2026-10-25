"""
Naive RAG エンジン（単純ベクトル検索）
"""

import time
import httpx
import chromadb
from config import settings


class RAGResult:
    """RAG実行結果のデータクラス"""

    def __init__(
        self,
        answer: str,
        chunks: list[dict],
        inference_time_ms: int,
    ):
        self.answer = answer
        self.chunks = chunks
        self.inference_time_ms = inference_time_ms

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "chunks": self.chunks,
            "inference_time_ms": self.inference_time_ms,
        }


def get_chroma_client():
    """ChromaDBクライアントを返す"""
    return chromadb.HttpClient(
        host=settings.CHROMA_HOST,
        port=settings.CHROMA_PORT,
    )


async def embed_texts_ollama(texts: list[str]) -> list[list[float]]:
    """Ollamaの/api/embedエンドポイントを使ってテキストをベクトル化する"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/embed",
            json={
                "model": settings.EMBED_MODEL,
                "input": texts,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("embeddings", [])


async def run_naive_rag(query: str, llm_model: str | None = None) -> dict:
    """
    単純ベクトル検索（Naive RAG）を実行する。

    メタデータフィルタを使わず、単純なベクトル類似度のみで
    ChromaDBからTop-4チャンクを検索し、Ollamaで回答を生成する。
    """
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    # ChromaDBのコレクションを取得
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )

    # クエリをOllamaでベクトル化
    query_embeddings = await embed_texts_ollama([query])

    # ベクトル検索（Top-4、フィルタなし）
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=4,
    )

    # 取得したチャンクを整形
    chunks = []
    ids_list = results.get("ids", [[]])[0]
    docs_list = results.get("documents", [[]])[0]
    metas_list = results.get("metadatas", [[]])[0]
    distances_list = results.get("distances", [[]])[0]

    for i in range(len(ids_list)):
        chunks.append(
            {
                "id": ids_list[i],
                "title": metas_list[i].get("title", ""),
                "tenant_id": metas_list[i].get("tenant_id", ""),
                "effective_year": metas_list[i].get("effective_year"),
                "text": docs_list[i],
                "distance": distances_list[i],
            }
        )

    # コンテキストを構築
    context_parts = []
    for c in chunks:
        context_parts.append(
            f"[{c['title']} ({c['tenant_id']}, {c['effective_year']})]\n{c['text']}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    # Ollama に推論を依頼
    start_time = time.time()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": llm_model,
                "prompt": (
                    "あなたは不動産管理SaaSの業務アシスタントです。"
                    "以下の参考情報に基づいて、質問に的確に回答してください。\n\n"
                    "=== 参考情報 ===\n"
                    f"{context_text}\n\n"
                    "=== 質問 ===\n"
                    f"{query}\n\n"
                    "参考情報に含まれていない場合は、「該当する情報が参考資料に見つかりませんでした」と回答してください。"
                ),
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

    inference_time_ms = int((time.time() - start_time) * 1000)

    return RAGResult(
        answer=data.get("response", ""),
        chunks=chunks,
        inference_time_ms=inference_time_ms,
    ).to_dict()
