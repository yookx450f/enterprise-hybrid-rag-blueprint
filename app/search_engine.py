"""
ハイブリッド検索エンジン（Metadata + BM25 + Vector RRF）
Phase 3: Naive RAG との違いを劇的にDemonstratingする核心モジュール
"""

import asyncio
import json
import logging
import time
import httpx
from typing import AsyncGenerator
from janome.tokenizer import Tokenizer as JanomeTokenizer
from rank_bm25 import BM25Okapi
from config import settings
from rag_engine import PipelineStep, get_chroma_client, embed_texts_ollama

logger = logging.getLogger(__name__)


# ─── 日本語トークナイズ ─────────────────────────────────────

_janome = JanomeTokenizer()

_NOUN_PARTS = {"名詞", "代名詞", "数"}
_VERB_ADJ_PARTS = {"動詞", "形容詞"}


def tokenize_ja(text: str) -> list[str]:
    """
    Janomeで日本語テキストを形態素解析し、
    名詞・動詞・形容詞（自立語）をリストで返す。
    動詞・形容詞は原形(base_form)を抽出し、重複トークンはフィルタする。
    
    注意: Janomeのバージョンによっては pos が None になる場合があるため、
    その場合は surface をそのままトークンとして扱う。
    """
    tokens = []
    seen = set()
    for token in _janome.tokenize(text):
        # posがNoneの場合はsurfaceをそのまま使用（辞書外語や固有名詞など）
        if token.pos is None:
            surface = token.surface
            if len(surface) >= 2 and surface not in seen:
                tokens.append(surface)
                seen.add(surface)
            continue
        
        base_pos = token.pos.split(",")[0]
        if base_pos in {"名詞", "代名詞", "助数詞"}:
            surface = token.surface
            if len(surface) >= 1 and surface not in seen:
                tokens.append(surface)
                seen.add(surface)
        elif base_pos in {"動詞", "形容詞"}:
            # Janomeでは base_form が原形（動詞・形容詞の基本形）
            base = token.base_form
            if len(base) >= 2 and base not in seen:
                tokens.append(base)
                seen.add(base)
    return tokens


# ─── BM25 スコアリング ─────────────────────────────────────

def compute_bm25_scores(
    query_tokens: list[str],
    doc_token_lists: list[list[str]],
) -> list[float]:
    """
    BM25Okapi を使って、クエリに対する各ドキュメントのキーワード関連性スコアを算出する。
    """
    if not doc_token_lists or not query_tokens:
        return [0.0] * len(doc_token_lists)

    # 空のトークンリストをフィルタ（BM25Okapiがゼロ除算するため）
    valid_indices = [i for i, tokens in enumerate(doc_token_lists) if tokens]
    if not valid_indices:
        return [0.0] * len(doc_token_lists)

    valid_docs = [doc_token_lists[i] for i in valid_indices]
    bm25 = BM25Okapi(valid_docs)
    valid_scores = bm25.get_scores(query_tokens)

    # 元のインデックス順に配置
    scores = [0.0] * len(doc_token_lists)
    for idx, score in zip(valid_indices, valid_scores):
        scores[idx] = score
    return scores


# ─── RRF 統合リランキング ──────────────────────────────────

def rrf_rerank(
    vector_ranked_ids: list[str],
    bm25_ranked_ids: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion (RRF) でベクトル検索とBM25検索の順位を統合する。
    rrf_score = 1 / (k + rank_vec) + 1 / (k + rank_bm25)

    戻り値: [(doc_id, rrf_score), ...] の降順リスト
    """
    score_map: dict[str, float] = {}
    for rank, doc_id in enumerate(vector_ranked_ids, start=1):
        score_map[doc_id] = score_map.get(doc_id, 0.0) + 1.0 / (k + rank)
    for rank, doc_id in enumerate(bm25_ranked_ids, start=1):
        score_map[doc_id] = score_map.get(doc_id, 0.0) + 1.0 / (k + rank)

    ranked = sorted(score_map.items(), key=lambda x: -x[1])
    return ranked


# ─── ハイブリッドRAG メイン関数 ─────────────────────────────

async def run_hybrid_rag(
    query: str,
    tenant_id: str = "A社_都市型",
    min_year: int = 2025,
    llm_model: str | None = None,
    ollama_url: str | None = None,
) -> dict:
    """
    ハイブリッド検索（Metadata + BM25 + Vector RRF）を実行する。

    Step 1: Metadata Pre-filtering で候補を絞り込む
    Step 2: 日本語形態素解析 + BM25 キーワードスコアリング
    Step 3: RRF 統合リランキングで上位3件を選定
    Step 4: 構造化プロンプトでLLM回答を生成
    """
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    steps: list[PipelineStep] = []

    # ── Step 0: クエリベクトル化 ───────────────────────
    t0 = time.time()
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    total_count = collection.count()
    logger.info(f"[Hybrid RAG] コレクション総数: {total_count}")

    query_embeddings = await embed_texts_ollama([query], ollama_url=ollama_url)
    embed_ms = int((time.time() - t0) * 1000)
    steps.append(PipelineStep("embed", "クエリベクトル化", "done", embed_ms, f"モデル: {settings.EMBED_MODEL}"))

    # ── Step 1: Metadata Pre-filtering ────────────────
    metadata_filter = {
        "$and": [
            {"tenant_id": {"$eq": tenant_id}},
            {"effective_year": {"$gte": min_year}},
        ]
    }

    t0 = time.time()
    vec_results = collection.query(
        query_embeddings=query_embeddings,
        n_results=10,
        where=metadata_filter,
    )
    filter_ms = int((time.time() - t0) * 1000)

    vec_ids = vec_results.get("ids", [[]])[0]
    vec_docs = vec_results.get("documents", [[]])[0]
    vec_metas = vec_results.get("metadatas", [[]])[0]
    vec_distances = vec_results.get("distances", [[]])[0]

    logger.info(f"[Hybrid RAG] メタフィルタ後ヒット数: {len(vec_ids)}")
    logger.info(f"[Hybrid RAG] フィルタ条件: tenant_id={tenant_id}, effective_year>={min_year}")
    if vec_metas:
        logger.info(f"[Hybrid RAG] 1件目のメタデータ: {vec_metas[0]}")

    steps.append(PipelineStep(
        "meta_filter", "メタデータフィルタ", "done", filter_ms,
        detail=f"tenant={tenant_id}, year>={min_year}",
        count_before=total_count,
        count_after=len(vec_ids),
    ))

    if not vec_ids:
        steps.append(PipelineStep("bm25", "BM25キーワードスコアリング", "skipped", detail="候補なしでスキップ"))
        steps.append(PipelineStep("rerank", "RRF統合リランキング", "skipped", detail="候補なしでスキップ"))
        return {
            "answer": "該当する情報がデータベースに見つかりませんでした。",
            "chunks": [],
            "inference_time_ms": 0,
            "prompt_length": 0,
            "pipeline_steps": [s.to_dict() for s in steps],
        }

    # ── Step 2: BM25 キーワードスコアリング ────────────
    t0 = time.time()
    query_tokens = tokenize_ja(query)
    doc_token_lists = [tokenize_ja(doc) for doc in vec_docs]
    bm25_scores = compute_bm25_scores(query_tokens, doc_token_lists)
    bm25_ms = int((time.time() - t0) * 1000)

    steps.append(PipelineStep(
        "bm25", "BM25キーワードスコアリング", "done", bm25_ms,
        detail=f"Janome形態素解析で {len(query_tokens)} 語抽出",
        query_keywords=query_tokens,
    ))

    # BM25スコアでソートしたIDリスト（降順＝スコア高的順）
    bm25_ranked_ids = [
        vec_ids[i]
        for i in sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])
    ]

    # ── Step 3: RRF 統合リランキング ──────────────────
    t0 = time.time()
    rrf_ranks = rrf_rerank(vec_ids, bm25_ranked_ids)
    rrf_ms = int((time.time() - t0) * 1000)

    steps.append(PipelineStep(
        "rerank", "RRF統合リランキング", "done", rrf_ms,
        detail=f"vec({len(vec_ids)}件) + BM25({len(bm25_ranked_ids)}件) RRF統合 → Top-3",
        count_before=len(vec_ids),
        count_after=3,
    ))

    # 上位3件のチャンクを選定（情報付きで整形）
    selected_chunks = []
    for doc_id, rrf_score in rrf_ranks[:3]:
        idx = vec_ids.index(doc_id)
        vec_rank = vec_ids.index(doc_id) + 1
        bm25_rank = bm25_ranked_ids.index(doc_id) + 1
        selected_chunks.append({
            "id": doc_id,
            "title": vec_metas[idx].get("title", ""),
            "tenant_id": vec_metas[idx].get("tenant_id", ""),
            "effective_year": vec_metas[idx].get("effective_year"),
            "text": vec_docs[idx],
            "vector_rank": vec_rank,
            "bm25_rank": bm25_rank,
            "rrf_score": round(rrf_score, 6),
            "distance": vec_distances[idx],
        })

    # ── Step 4: 構造化プロンプトでLLM回答生成 ───────────
    context_parts = []
    for c in selected_chunks:
        context_parts.append(
            f"[{c['title']} ({c['tenant_id']}, {c['effective_year']})]\n{c['text']}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    prompt = (
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
    )

    base_url = ollama_url or settings.OLLAMA_BASE_URL
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/api/generate",
            json={
                    "model": llm_model,
                    "prompt": prompt,
                    "stream": False,
                },
            timeout=settings.LLM_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json()

    # Ollamaサーバー側の実際の推論時間を使用（キュー待ち時間除外）
    eval_duration_ns = data.get("eval_duration", 0)
    inference_time_ms = int(eval_duration_ns / 1_000_000) if eval_duration_ns else 0
    prompt_length = len(prompt)

    steps.append(PipelineStep("llm", "LLM推論", "done", inference_time_ms, f"モデル: {llm_model}"))

    return {
        "answer": data.get("response", ""),
        "chunks": selected_chunks,
        "inference_time_ms": inference_time_ms,
        "prompt_length": prompt_length,
        "pipeline_steps": [s.to_dict() for s in steps],
    }


async def stream_hybrid_rag(
    query: str,
    tenant_id: str = "A社_都市型",
    min_year: int = 2025,
    llm_model: str | None = None,
    ollama_url: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    ストリーミング版 Hybrid RAG。
    各ステップ完了時にイベントを yield する。
    """
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    # ── Step 0: クエリベクトル化 ───────────────────────
    yield {"type": "step_start", "step_id": "embed", "name": "クエリベクトル化"}
    t0 = time.time()
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    total_count = collection.count()
    logger.info(f"[Hybrid RAG Stream] コレクション総数: {total_count}, クエリ: {query[:30]}...")

    query_embeddings = await embed_texts_ollama([query], ollama_url=ollama_url)
    embed_ms = int((time.time() - t0) * 1000)
    yield {
        "type": "step_done",
        "step_id": "embed",
        "duration_ms": embed_ms,
        "detail": f"モデル: {settings.EMBED_MODEL}",
    }

    # ── Step 1: Metadata Pre-filtering ────────────────
    yield {"type": "step_start", "step_id": "meta_filter", "name": "メタデータフィルタ"}
    metadata_filter = {
        "$and": [
            {"tenant_id": {"$eq": tenant_id}},
            {"effective_year": {"$gte": min_year}},
        ]
    }

    t0 = time.time()
    vec_results = collection.query(
        query_embeddings=query_embeddings,
        n_results=10,
        where=metadata_filter,
    )
    filter_ms = int((time.time() - t0) * 1000)

    vec_ids = vec_results.get("ids", [[]])[0]
    vec_docs = vec_results.get("documents", [[]])[0]
    vec_metas = vec_results.get("metadatas", [[]])[0]
    vec_distances = vec_results.get("distances", [[]])[0]

    logger.info(f"[Hybrid RAG Stream] メタフィルタ後ヒット数: {len(vec_ids)}")
    logger.info(f"[Hybrid RAG Stream] フィルタ条件: tenant_id={tenant_id}, effective_year>={min_year}")
    if vec_metas:
        for m in vec_metas[:3]:
            ey = m.get('effective_year')
            logger.info(f"[Hybrid RAG Stream] メタ: tenant={m.get('tenant_id')}, year={ey}({type(ey).__name__}), title={m.get('title','')[:20]}")

    yield {
        "type": "step_done",
        "step_id": "meta_filter",
        "duration_ms": filter_ms,
        "detail": f"tenant={tenant_id}, year>={min_year}",
        "count_before": total_count,
        "count_after": len(vec_ids),
    }

    if not vec_ids:
        yield {
            "type": "step_done",
            "step_id": "bm25",
            "duration_ms": 0,
            "status": "skipped",
            "detail": "候補なしでスキップ",
        }
        yield {
            "type": "step_done",
            "step_id": "rerank",
            "duration_ms": 0,
            "status": "skipped",
            "detail": "候補なしでスキップ",
        }
        yield {
            "type": "answer",
            "answer": "該当する情報がデータベースに見つかりませんでした。",
            "inference_time_ms": 0,
        }
        yield {"type": "done"}
        return

    # ── Step 2: BM25 キーワードスコアリング ────────────
    yield {"type": "step_start", "step_id": "bm25", "name": "BM25キーワードスコアリング"}
    t0 = time.time()
    query_tokens = tokenize_ja(query)
    doc_token_lists = [tokenize_ja(doc) for doc in vec_docs]
    bm25_scores = compute_bm25_scores(query_tokens, doc_token_lists)
    bm25_ms = int((time.time() - t0) * 1000)

    yield {
        "type": "step_done",
        "step_id": "bm25",
        "duration_ms": bm25_ms,
        "detail": f"Janome形態素解析で {len(query_tokens)} 語抽出",
        "query_keywords": query_tokens,
    }

    # BM25スコアでソートしたIDリスト（降順＝スコア高的順）
    bm25_ranked_ids = [
        vec_ids[i]
        for i in sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])
    ]

    # ── Step 3: RRF 統合リランキング ──────────────────
    yield {"type": "step_start", "step_id": "rerank", "name": "RRF統合リランキング"}
    t0 = time.time()
    rrf_ranks = rrf_rerank(vec_ids, bm25_ranked_ids)
    rrf_ms = int((time.time() - t0) * 1000)

    yield {
        "type": "step_done",
        "step_id": "rerank",
        "duration_ms": rrf_ms,
        "detail": f"vec({len(vec_ids)}件) + BM25({len(bm25_ranked_ids)}件) RRF統合 → Top-3",
        "count_before": len(vec_ids),
        "count_after": 3,
    }

    # 上位3件のチャンクを選定（情報付きで整形）
    selected_chunks = []
    for doc_id, rrf_score in rrf_ranks[:3]:
        idx = vec_ids.index(doc_id)
        vec_rank = vec_ids.index(doc_id) + 1
        bm25_rank = bm25_ranked_ids.index(doc_id) + 1
        selected_chunks.append({
            "id": doc_id,
            "title": vec_metas[idx].get("title", ""),
            "tenant_id": vec_metas[idx].get("tenant_id", ""),
            "effective_year": vec_metas[idx].get("effective_year"),
            "text": vec_docs[idx],
            "vector_rank": vec_rank,
            "bm25_rank": bm25_rank,
            "rrf_score": round(rrf_score, 6),
            "distance": vec_distances[idx],
        })

    # ── チャンクプレビュー送信 ─────────────────────────
    preview_chunks = []
    for c in selected_chunks:
        text_preview = c["text"][:100] + "..." if len(c["text"]) > 100 else c["text"]
        preview_chunks.append({
            "id": c["id"],
            "title": c["title"],
            "text_preview": text_preview,
            "vector_rank": c["vector_rank"],
            "bm25_rank": c["bm25_rank"],
            "rrf_score": c["rrf_score"],
        })
    yield {"type": "chunk_preview", "chunks": preview_chunks}

    # ── Step 4: 構造化プロンプトでLLM回答生成 ───────────
    context_parts = []
    for c in selected_chunks:
        context_parts.append(
            f"[{c['title']} ({c['tenant_id']}, {c['effective_year']})]\n{c['text']}"
        )
    context_text = "\n\n---\n\n".join(context_parts)

    prompt = (
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
    )

    yield {"type": "step_start", "step_id": "llm", "name": "LLM推論"}
    full_answer = ""
    final_eval_duration_ns = 0
    base_url = ollama_url or settings.OLLAMA_BASE_URL
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        async with client.stream("POST",
            f"{base_url}/api/generate",
            json={
                     "model": llm_model,
                     "prompt": prompt,
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
