"""
FastAPI アプリケーション（Phase 1 + Phase 2 + Phase 3 + Phase 4）
"""

import asyncio
import httpx
from pydantic import BaseModel, Field
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from config import settings

from seed_data import seed_all_data
from rag_engine import run_naive_rag
from search_engine import run_hybrid_rag
from daily_summary import run_daily_summary
from collection_browser import query_collection, get_unique_values

app = FastAPI(title="Enterprise Hybrid RAG")

# Phase 4: テンプレートエンジン設定
templates = Jinja2Templates(directory="templates")


# ─── Pydantic モデル ──────────────────────────────────────

class NaiveRAGRequest(BaseModel):
    query: str = Field(..., description="検索・質問文", example="A社におけるフリーランス・個人事業主の入居審査の特例条件と注意点を教えて")
    llm_model: str | None = Field(None, description="使用するLLMモデル名（未指定時はデフォルト）", example="qwen3.6:27b")


class CompareRAGRequest(BaseModel):
    query: str = Field(..., description="検索・質問文", example="A社におけるフリーランス・個人事業主の入居審査の特例条件と注意点を教えて")
    tenant_id: str = Field("A社_都市型", description="テナントID", example="A社_都市型")
    min_year: int = Field(2025, description="対象年以降のフィルタ", example=2025)
    llm_model: str | None = Field(None, description="使用するLLMモデル名（未指定時はデフォルト）", example="qwen3.6:27b")


class DailySummaryRequest(BaseModel):
    llm_model: str | None = Field(None, description="使用するLLMモデル名（未指定時はデフォルト）", example="qwen3.6:27b")


# ─── Phase 1: ヘルスチェック ──────────────────────────────

@app.get("/api/health")
async def health_check():
    """ChromaDB と Ollama の疎通確認エンドポイント"""
    result = {
        "status": "ok",
        "chromadb": "unknown",
        "ollama_url": settings.OLLAMA_BASE_URL,
        "ollama_status": "unknown",
        "available_models": [],
    }

    # ChromaDB 疎通確認
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://{settings.CHROMA_HOST}:{settings.CHROMA_PORT}/api/v1/heartbeat",
                timeout=5.0,
            )
            if resp.status_code == 200:
                result["chromadb"] = "connected"
            else:
                result["chromadb"] = f"error_status_{resp.status_code}"
    except Exception as e:
        result["chromadb"] = f"error:{e}"

    # Ollama 疎通確認
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.OLLAMA_BASE_URL}/api/tags",
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                result["ollama_status"] = "connected"
                result["available_models"] = [
                    m.get("name", "") for m in data.get("models", [])
                ]
            else:
                result["ollama_status"] = f"error_status_{resp.status_code}"
    except Exception as e:
        result["ollama_status"] = f"error:{e}"

    # 全体ステータスの決定
    if result["chromadb"] != "connected" or result["ollama_status"] != "connected":
        result["status"] = "degraded"

    return result


# ─── Phase 2: データ投入 ──────────────────────────────────

@app.post("/api/seed")
async def seed_endpoint():
    """業務ダミーデータを ChromaDB に投入する"""
    try:
        result = await seed_all_data()
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Phase 2: Naive RAG ──────────────────────────────────

@app.post("/api/naive_rag")
async def naive_rag_endpoint(req: NaiveRAGRequest):
    """単純ベクトル検索（Naive RAG）を実行する"""
    try:
        result = await run_naive_rag(
            query=req.query,
            llm_model=req.llm_model,
        )
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Phase 3: 比較エンドポイント ─────────────────────────

@app.post("/api/compare_rag")
async def compare_rag_endpoint(req: CompareRAGRequest):
    """
    Naive RAG と Hybrid RAG の結果を並列実行して比較する。
    """
    try:
        naive_task = run_naive_rag(query=req.query, llm_model=req.llm_model)
        hybrid_task = run_hybrid_rag(
            query=req.query,
            tenant_id=req.tenant_id,
            min_year=req.min_year,
            llm_model=req.llm_model,
        )
        naive_result, hybrid_result = await asyncio.gather(naive_task, hybrid_task)
        return {
            "naive_rag": naive_result,
            "hybrid_rag": hybrid_result,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Phase 3: 日次要約エンドポイント ─────────────────────

@app.post("/api/daily_summary")
async def daily_summary_endpoint(req: DailySummaryRequest):
    """店舗メール5件の一括要約・分類結果を返す"""
    try:
        result = await run_daily_summary(llm_model=req.llm_model)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Phase 5: コレクションブラウザ ──────────────────────

@app.get("/api/collection/browser")
async def collection_browser_endpoint(
    tenant_id: str | None = None,
    doc_type: str | None = None,
    effective_year: int | None = None,
    search_text: str | None = None,
    offset: int = 0,
    limit: int = 50,
):
    """ChromaDBコレクション内のドキュメント一覧を取得する"""
    try:
        result = query_collection(
            tenant_id=tenant_id,
            doc_type=doc_type,
            effective_year=effective_year,
            search_text=search_text,
            offset=offset,
            limit=limit,
        )
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/collection/values")
async def collection_values_endpoint():
    """コレクション内のメタデータユニーク値を取得する（フィルタ用）"""
    try:
        result = get_unique_values()
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── Phase 4: ダッシュボードトップページ ──────────────────

from fastapi.responses import HTMLResponse
from fastapi.requests import Request

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Webダッシュボードを返す"""
    return templates.TemplateResponse("index.html", {"request": request})


# 静的ファイルマウント（最後に配置 must be last）
app.mount("/static", StaticFiles(directory="static"), name="static")
