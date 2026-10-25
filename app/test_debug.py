#!/usr/bin/env python
import traceback
import sys
import httpx
import chromadb

try:
    print(f"chromadb version: {chromadb.__version__}", flush=True)
    
    client = chromadb.HttpClient(host="chromadb", port=8000)
    # embedding_function=None で取得（自動エンベッディングを無効化）
    collection = client.get_collection(
        name="b2b_tenant_rules",
        embedding_function=None,
    )
    print(f"Count: {collection.count()}", flush=True)

    # Ollamaでクエリのベクトルを取得
    resp = httpx.post(
        "http://host.docker.internal:11434/api/embed",
        json={"model": "bge-m3", "input": ["フリーランス審査"]},
    )
    q_vec = resp.json()["embeddings"][0]
    print(f"Query vec dim: {len(q_vec)}", flush=True)

    # query_embeddings で検索
    results = collection.query(query_embeddings=[q_vec], n_results=2)
    print(f"Keys: {list(results.keys())}", flush=True)
    print(f"IDs: {results.get('ids')}", flush=True)

except Exception as e:
    traceback.print_exc()
    sys.exit(1)
