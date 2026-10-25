"""
Naive RAG エンジン（単純ベクトル検索）
"""

import time
import httpx
import chromadb
from typing import AsyncGenerator
from config import settings


class PipelineStep:
    """検索パイプラインの各ステップ情報"""

    def __init__(
        self,
        step_id: str,
        name: str,
        status: str,  # "done" | "skipped" | "warning"
        duration_ms: int = 0,
        detail: str = "",
        count_before: int | None = None,
        count_after: int | None = None,
    ):
        self.step_id = step_id
        self.name = name
        self.status = status
        self.duration_ms = duration_ms
        self.detail = detail
        self.count_before = count_before
        self.count_after = count_after

    def to_dict(self) -> dict:
        d: dict = {
            "step_id": self.step_id,
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }
        if self.count_before is not None:
            d["count_before"] = self.count_before
        if self.count_after is not None:
            d["count_after"] = self.count_after
        return d


class RAGResult:
    """RAG実行結果のデータクラス"""

    def __init__(
        self,
        answer: str,
        chunks: list[dict],
        inference_time_ms: int,
        pipeline_steps: list[PipelineStep] | None = None,
    ):
        self.answer = answer
        self.chunks = chunks
        self.inference_time_ms = inference_time_ms
        self.pipeline_steps = pipeline_steps or []

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "chunks": self.chunks,
            "inference_time_ms": self.inference_time_ms,
            "pipeline_steps": [s.to_dict() for s in self.pipeline_steps],
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

    steps: list[PipelineStep] = []

    # ── Step 1: クエリベクトル化 ───────────────────────
    t0 = time.time()
    query_embeddings = await embed_texts_ollama([query])
    embed_ms = int((time.time() - t0) * 1000)
    steps.append(PipelineStep("embed", "クエリベクトル化", "done", embed_ms, f"モデル: {settings.EMBED_MODEL}"))

    # ChromaDBのコレクションを取得
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    total_count = collection.count()

    # ── Step 2: ベクトル検索 ───────────────────────────
    t0 = time.time()
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=4,
    )
    vec_ms = int((time.time() - t0) * 1000)
    steps.append(PipelineStep(
        "vector_search", "ベクトル検索", "done", vec_ms,
        detail=f"類似度ベース Top-4 選定",
        count_before=total_count,
        count_after=4,
    ))

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

    # ── Step 3: LLM推論 ────────────────────────────────
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
    steps.append(PipelineStep("llm", "LLM推論", "done", inference_time_ms, f"モデル: {llm_model}"))

    return RAGResult(
        answer=data.get("response", ""),
        chunks=chunks,
        inference_time_ms=inference_time_ms,
        pipeline_steps=steps,
    ).to_dict()


async def stream_naive_rag(
    query: str,
    llm_model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    ストリーミング版 Naive RAG。
    各ステップ完了時にイベントを yield する。
    """
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    # ── Step 1: クエリベクトル化 ───────────────────────
    yield {"type": "step_start", "step_id": "embed", "name": "クエリベクトル化"}
    t0 = time.time()
    query_embeddings = await embed_texts_ollama([query])
    embed_ms = int((time.time() - t0) * 1000)
    yield {
        "type": "step_done",
        "step_id": "embed",
        "duration_ms": embed_ms,
        "detail": f"モデル: {settings.EMBED_MODEL}",
    }

    # ChromaDB コレクションを取得
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    total_count = collection.count()

    # ── Step 2: ベクトル検索 ───────────────────────────
    yield {"type": "step_start", "step_id": "vector_search", "name": "ベクトル検索"}
    t0 = time.time()
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=4,
    )
    vec_ms = int((time.time() - t0) * 1000)

    ids_list = results.get("ids", [[]])[0]
    docs_list = results.get("documents", [[]])[0]
    metas_list = results.get("metadatas", [[]])[0]
    distances_list = results.get("distances", [[]])[0]

    yield {
        "type": "step_done",
        "step_id": "vector_search",
        "duration_ms": vec_ms,
        "detail": "類似度ベース Top-4 選定",
        "count_before": total_count,
        "count_after": len(ids_list),
    }

    # ── チャンクプレビュー送信 ─────────────────────────
    preview_chunks = []
    for i in range(len(ids_list)):
        text_preview = docs_list[i][:100] + "..." if len(docs_list[i]) > 100 else docs_list[i]
        preview_chunks.append({
            "id": ids_list[i],
            "title": metas_list[i].get("title", ""),
            "text_preview": text_preview,
            "distance": round(distances_list[i], 4),
        })
    yield {"type": "chunk_preview", "chunks": preview_chunks}

    # ── コンテキスト構築 + LLM推論 ─────────────────────
    context_parts = []
    for i in range(len(ids_list)):
        context_parts.append(
            f"[{metas_list[i].get('title', '')} ({metas_list[i].get('tenant_id', '')}, {metas_list[i].get('effective_year')})]\n{docs_list[i]}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    yield {"type": "step_start", "step_id": "llm", "name": "LLM推論"}
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
    yield {
        "type": "step_done",
        "step_id": "llm",
        "duration_ms": inference_time_ms,
        "detail": f"モデル: {llm_model}",
    }

    answer = data.get("response", "")
    # 回答を複数チャンクに分割してストリーミング表示
    chunk_size = 50
    for i in range(0, len(answer), chunk_size):
        yield {
            "type": "answer_chunk",
            "text": answer[i:i + chunk_size],
            "inference_time_ms": inference_time_ms,
        }

    yield {"type": "done"}
