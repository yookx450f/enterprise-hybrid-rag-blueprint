"""
Naive RAG エンジン（単純ベクトル検索）
"""

import json
import logging
import re
import time
import httpx
import chromadb
from typing import AsyncGenerator
from config import settings, resolve_ollama_url

logger = logging.getLogger(__name__)


# ─── 汚染判定ユーティリティ ──────────────────────────────────────

def extract_pollution_keywords(text: str, tenant_id: str) -> list[str]:
    """
    汚染チャンクから、回答文中で誤情報を示す可能性のある固有名詞を抽出する。
    """
    keywords = []
    # テナント名自体がキーワード（A社以外の場合）
    if tenant_id and tenant_id != "A社_都市型":
        keywords.append(tenant_id)
    # 支店名などの固有名詞パターン
    branch_matches = re.findall(r'([^\s]{2,4}支店)', text)
    keywords.extend(branch_matches)
    return list(dict.fromkeys(keywords))  # 重複除去して返す


def check_chunk_pollution(tenant_id: str, effective_year, target_tenant: str = "A社_都市型", min_year: int = 2025):
    """
    チャンクのメタデータを検証し、汚染判定を行う。
    
    Returns: (pollution_reason, pollution_keywords)
        - 汚染なし: (None, [])
        - 汚染あり: ("理由文字列", ["キーワード1", ...])
    """
    if tenant_id != target_tenant:
        return f"別拠点のローカル特例（{tenant_id}）", []
    if effective_year and effective_year < min_year:
        return f"廃止済み旧規定（{effective_year}年）", []
    return None, []


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
        query_keywords: list[str] | None = None,
    ):
        self.step_id = step_id
        self.name = name
        self.status = status
        self.duration_ms = duration_ms
        self.detail = detail
        self.count_before = count_before
        self.count_after = count_after
        self.query_keywords = query_keywords

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
        if self.query_keywords:
            d["query_keywords"] = self.query_keywords
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


async def embed_texts_ollama(texts: list[str], ollama_url: str | None = None) -> list[list[float]]:
    """Ollamaの/api/embedエンドポイントを使ってテキストをベクトル化する"""
    base_url = ollama_url or settings.OLLAMA_BASE_URL
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/api/embed",
            json={
                "model": settings.EMBED_MODEL,
                "input": texts,
            },
            timeout=settings.EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("embeddings", [])


async def run_naive_rag(query: str, llm_model: str | None = None, ollama_url: str | None = None) -> dict:
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
    query_embeddings = await embed_texts_ollama([query], ollama_url=ollama_url)
    embed_ms = int((time.time() - t0) * 1000)
    steps.append(PipelineStep("embed", "クエリベクトル化", "done", embed_ms, f"モデル: {settings.EMBED_MODEL}"))

    # ChromaDBのコレクションを取得
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    total_count = collection.count()
    logger.info(f"[Naive RAG] コレクション総数: {total_count}")

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

    logger.info(f"[Naive RAG] ベクトル検索ヒット数: {len(ids_list)}, IDs: {ids_list}")
    if metas_list:
        for m in metas_list[:3]:
            ey = m.get('effective_year')
            logger.info(f"[Naive RAG] メタ: tenant={m.get('tenant_id')}, year={ey}({type(ey).__name__}), title={m.get('title','')[:20]}")

    for i in range(len(ids_list)):
        chunk_tenant_id = metas_list[i].get("tenant_id", "")
        chunk_effective_year = metas_list[i].get("effective_year")
        pollution_reason, _ = check_chunk_pollution(chunk_tenant_id, chunk_effective_year)
        
        chunk_data = {
            "id": ids_list[i],
            "title": metas_list[i].get("title", ""),
            "tenant_id": chunk_tenant_id,
            "effective_year": chunk_effective_year,
            "text": docs_list[i],
            "distance": distances_list[i],
        }
        if pollution_reason:
            chunk_data["pollution_reason"] = pollution_reason
            chunk_data["pollution_keywords"] = extract_pollution_keywords(docs_list[i], chunk_tenant_id)
        chunks.append(chunk_data)

    # コンテキストを構築
    context_parts = []
    for c in chunks:
        context_parts.append(
            f"[{c['title']} ({c['tenant_id']}, {c['effective_year']})]\n{c['text']}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    # ── Step 3: LLM推論 ────────────────────────────────
    async with httpx.AsyncClient() as client:
        base_url = ollama_url or settings.OLLAMA_BASE_URL
        resp = await client.post(
            f"{base_url}/api/generate",
            json={
                "model": llm_model,
                "prompt": (
                    "あなたは不動産管理SaaSの業務アシスタントです。"
                    "以下の参考情報に基づいて、質問に的確に回答してください。\n\n"
                    "=== 参考情報 ===\n"
                    f"{context_text}\n\n"
                    "=== 質問 ===\n"
                    f"{query}\n\n"
                    "=== 回答フォーマット ===\n"
                    "以下の4項目を明確に回答してください。\n"
                    "必ずMarkdown形式で回答し、## で見出し、### で各項目の小見出し、箇条書き（-）で詳細を記述してください。\n\n"
                    "## {質問の要約}\n\n"
                    "### ① 審査可否の結論\n"
                    "- ...\n\n"
                    "### ② 特例適用の必須条件\n"
                    "- ...\n\n"
                    "### ③ 過去トラブル事例を踏まえた審査時の注意点\n"
                    "- ...\n\n"
                    "### ④ 根拠とした規定タイトル\n"
                    "- ...\n\n"
                    "参考情報に含まれていない場合は、「該当する情報が参考資料に見つかりませんでした」と回答してください。"
                ),
                "stream": False,
            },
            timeout=settings.LLM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

    # Ollamaサーバー側の実際の推論時間を使用（キュー待ち時間除外）
    eval_duration_ns = data.get("eval_duration", 0)
    inference_time_ms = int(eval_duration_ns / 1_000_000) if eval_duration_ns else 0
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
    ollama_url: str | None = None,
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
    query_embeddings = await embed_texts_ollama([query], ollama_url=ollama_url)
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
    logger.info(f"[Naive RAG Stream] コレクション総数: {total_count}")

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

    logger.info(f"[Naive RAG Stream] ベクトル検索ヒット数: {len(ids_list)}, IDs: {ids_list}")
    if metas_list:
        for m in metas_list[:3]:
            ey = m.get('effective_year')
            logger.info(f"[Naive RAG Stream] メタ: tenant={m.get('tenant_id')}, year={ey}({type(ey).__name__}), title={m.get('title','')[:20]}")

    yield {
        "type": "step_done",
        "step_id": "vector_search",
        "duration_ms": vec_ms,
        "detail": "類似度ベース Top-4 選定",
        "count_before": total_count,
        "count_after": len(ids_list),
    }

    # ── チャンクプレビュー送信（汚染判定付き） ──────────────
    preview_chunks = []
    all_pollution_keywords = []
    for i in range(len(ids_list)):
        text_preview = docs_list[i][:100] + "..." if len(docs_list[i]) > 100 else docs_list[i]
        chunk_tenant_id = metas_list[i].get("tenant_id", "")
        chunk_effective_year = metas_list[i].get("effective_year")
        
        # 汚染判定
        pollution_reason, pollution_kws = check_chunk_pollution(
            chunk_tenant_id, chunk_effective_year
        )
        if pollution_kws:
            all_pollution_keywords.extend(pollution_kws)
        # テナント名がA社以外の場合、そのテキストから固有名詞を抽出
        if pollution_reason:
            extracted_kws = extract_pollution_keywords(docs_list[i], chunk_tenant_id)
            all_pollution_keywords.extend(extracted_kws)
        
        chunk_data = {
            "id": ids_list[i],
            "title": metas_list[i].get("title", ""),
            "text_preview": text_preview,
            "distance": round(distances_list[i], 4),
        }
        if pollution_reason:
            chunk_data["pollution_reason"] = pollution_reason
            chunk_data["pollution_keywords"] = extract_pollution_keywords(docs_list[i], chunk_tenant_id)
        preview_chunks.append(chunk_data)
    yield {"type": "chunk_preview", "chunks": preview_chunks}

    # ── コンテキスト構築 + LLM推論 ─────────────────────
    context_parts = []
    for i in range(len(ids_list)):
        context_parts.append(
            f"[{metas_list[i].get('title', '')} ({metas_list[i].get('tenant_id', '')}, {metas_list[i].get('effective_year')})]\n{docs_list[i]}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    yield {"type": "step_start", "step_id": "llm", "name": "LLM推論"}
    full_answer = ""
    final_eval_duration_ns = 0
    base_url = ollama_url or settings.OLLAMA_BASE_URL
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        async with client.stream("POST",
            f"{base_url}/api/generate",
            json={
                "model": llm_model,
                "prompt": (
                    "あなたは不動産管理SaaSの業務アシスタントです。"
                    "以下の参考情報に基づいて、質問に的確に回答してください。\n\n"
                    "=== 参考情報 ===\n"
                    f"{context_text}\n\n"
                    "=== 質問 ===\n"
                    f"{query}\n\n"
                    "=== 回答フォーマット ===\n"
                    "以下の4項目を明確に回答してください。\n"
                    "必ずMarkdown形式で回答し、## で見出し、### で各項目の小見出し、箇条書き（-）で詳細を記述してください。\n\n"
                    "## {質問の要約}\n\n"
                    "### ① 審査可否の結論\n"
                    "- ...\n\n"
                    "### ② 特例適用の必須条件\n"
                    "- ...\n\n"
                    "### ③ 過去トラブル事例を踏まえた審査時の注意点\n"
                    "- ...\n\n"
                    "### ④ 根拠とした規定タイトル\n"
                    "- ...\n\n"
                    "参考情報に含まれていない場合は、「該当する情報が参考資料に見つかりませんでした」と回答してください。"
                ),
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        full_answer += token
                        yield {
                            "type": "answer_chunk",
                            "text": token,
                            "inference_time_ms": int(chunk.get("eval_duration", 0) / 1_000_000),
                        }
                    if chunk.get("done", False):
                        final_eval_duration_ns = chunk.get("eval_duration", 0)
                        break
                except json.JSONDecodeError:
                    pass

    # Ollamaサーバー側の実際の推論時間を使用（キュー待ち時間除外）
    inference_time_ms = int(final_eval_duration_ns / 1_000_000) if final_eval_duration_ns else 0
    yield {
        "type": "step_done",
        "step_id": "llm",
        "duration_ms": inference_time_ms,
        "detail": f"モデル: {llm_model}",
    }

    yield {"type": "done"}
