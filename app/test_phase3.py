"""Phase 3: /api/compare_rag エンドポイントの動作確認"""

import httpx
import json


async def test_compare_rag():
    url = "http://localhost:8080/api/compare_rag"
    payload = {
        "query": "A社におけるフリーランス・個人事業主の入居審査の特例条件と注意点を教えて",
        "tenant_id": "A社_都市型",
        "min_year": 2025,
        "llm_model": "qwen27b-zoo",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=180.0)
        print(f"Status: {resp.status_code}")
        data = resp.json()

        if "naive_rag" in data and "hybrid_rag" in data:
            # Naive RAG のチャンクを表示
            naive_chunks = data["naive_rag"].get("chunks", [])
            print("\n=== Naive RAG チャンク ===")
            for c in naive_chunks:
                print(f"  - {c.get('title')} (tenant: {c.get('tenant_id')}, year: {c.get('effective_year')})")

            # Hybrid RAG のチャンクを表示
            hybrid_chunks = data["hybrid_rag"].get("chunks", [])
            print("\n=== Hybrid RAG チャンク ===")
            for c in hybrid_chunks:
                print(
                    f"  - {c.get('title')} "
                    f"(tenant: {c.get('tenant_id')}, year: {c.get('effective_year')}) "
                    f"[vec_rank={c.get('vector_rank')}, bm25_rank={c.get('bm25_rank')}, rrf={c.get('rrf_score')}]"
                )

            # 簡易回答を切り出して表示
            naive_answer = data["naive_rag"].get("answer", "")[:300]
            hybrid_answer = data["hybrid_rag"].get("answer", "")[:300]
            print("\n=== Naive RAG 回答 (冒頭) ===")
            print(naive_answer)
            print("\n=== Hybrid RAG 回答 (冒頭) ===")
            print(hybrid_answer)
        else:
            print(f"Error: {data}")


async def test_daily_summary():
    url = "http://localhost:8080/api/daily_summary"

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"llm_model": "qwen27b-zoo"}, timeout=180.0)
        print(f"\nStatus: {resp.status_code}")
        data = resp.json()
        answer = data.get("answer", "")
        print(f"=== Daily Summary (冒頭 500文字) ===")
        print(answer[:500])


if __name__ == "__main__":
    import asyncio

    print("--- Phase 3 Test: /api/compare_rag ---")
    asyncio.run(test_compare_rag())
    
    print("\n\n--- Phase 3 Test: /api/daily_summary ---")
    asyncio.run(test_daily_summary())
