"""
ハイブリッド検索エンジン（Metadata + BM25 + Vector RRF）
Phase 3: Naive RAG との違いを劇的にDemonstratingする核心モジュール
"""

import time
import httpx
from janome.tokenizer import Tokenizer as JanomeTokenizer
from rank_bm25 import BM25Okapi
from config import settings
from rag_engine import get_chroma_client, embed_texts_ollama


# ─── 日本語トークナイズ ─────────────────────────────────────

_janome = JanomeTokenizer()

_NOUN_PARTS = {"名詞", "代名詞", "数"}
_VERB_ADJ_PARTS = {"動詞", "形容詞"}


def tokenize_ja(text: str) -> list[str]:
    """
    Janomeで日本語テキストを形態素解析し、
    名詞・動詞・形容詞（自立語）の表層形をリストで返す。
    """
    tokens = []
    for token in _janome.tokenize(text):
        # posはカンマ区切りの文字列（例: "名詞,固有名詞,組織"）
        base_pos = token.pos.split(",")[0] if token.pos else ""
        if base_pos in {"名詞", "代名詞", "助数詞"} or base_pos in {"動詞", "形容詞"}:
            tokens.append(token.surface)
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

    # ── Step 1: Metadata Pre-filtering ──────────────
    client = get_chroma_client()
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )

    metadata_filter = {
        "$and": [
            {"tenant_id": {"$eq": tenant_id}},
            {"effective_year": {"$gte": min_year}},
        ]
    }

    # クエリをOllamaでベクトル化
    query_embeddings = await embed_texts_ollama([query])

    # ベクトル検索（メタデータフィルタ付き、Top-10候補取得）
    vec_results = collection.query(
        query_embeddings=query_embeddings,
        n_results=10,
        where=metadata_filter,
    )

    vec_ids = vec_results.get("ids", [[]])[0]
    vec_docs = vec_results.get("documents", [[]])[0]
    vec_metas = vec_results.get("metadatas", [[]])[0]
    vec_distances = vec_results.get("distances", [[]])[0]

    if not vec_ids:
        return {
            "answer": "該当する情報がデータベースに見つかりませんでした。",
            "chunks": [],
            "inference_time_ms": 0,
            "prompt_length": 0,
        }

    # ── Step 2: BM25 キーワードスコアリング ──────────
    query_tokens = tokenize_ja(query)
    doc_token_lists = [tokenize_ja(doc) for doc in vec_docs]
    bm25_scores = compute_bm25_scores(query_tokens, doc_token_lists)

    # BM25スコアでソートしたIDリスト（降順＝スコア高的順）
    bm25_ranked_ids = [
        vec_ids[i]
        for i in sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])
    ]

    # ── Step 3: RRF 統合リランキング ────────────────
    rrf_ranks = rrf_rerank(vec_ids, bm25_ranked_ids)

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

    # ── Step 4: 構造化プロンプトでLLM回答生成 ────────
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
        "以下の4項目を明確に回答してください：\n"
        "① 審査可否の結論\n"
        "② 特例適用の必須条件\n"
        "③ 過去トラブル事例を踏まえた審査時の注意点\n"
        "④ 根拠とした規定タイトル\n\n"
        "参考情報に含まれていない場合は、「該当する情報が参考資料に見つかりませんでした」と回答してください。"
    )

    start_time = time.time()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": llm_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

    inference_time_ms = int((time.time() - start_time) * 1000)
    prompt_length = len(prompt)

    return {
        "answer": data.get("response", ""),
        "chunks": selected_chunks,
        "inference_time_ms": inference_time_ms,
        "prompt_length": prompt_length,
    }
