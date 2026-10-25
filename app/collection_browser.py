"""
コレクションブラウザ用ヘルパー関数
ChromaDBコレクション内のドキュメント一覧取得・フィルタリングを行う
"""

import chromadb
from config import settings


COLLECTION_NAME = "b2b_tenant_rules"


def get_chroma_client():
    """ChromaDBクライアントを返す"""
    return chromadb.HttpClient(
        host=settings.CHROMA_HOST,
        port=settings.CHROMA_PORT,
    )


def query_collection(
    tenant_id: str | None = None,
    doc_type: str | None = None,
    effective_year: int | None = None,
    search_text: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """
    ChromaDBコレクションからドキュメント一覧を取得する。

    - メタデータフィルタ (tenant_id, doc_type, effective_year) をChromaDB側に適用
    - テキスト検索 (search_text) はクライアント側で二次フィルタ
    - ページネーション対応

    戻り値: {
        "total_count": int,       # コレクション内全件数（フィルタ前）
        "filtered_count": int,    # メタフィルタ後の件数
        "documents": [...],       # ページネーション適用後のドキュメントリスト
        "collection_metadata": {...},
    }
    """
    client = get_chroma_client()

    try:
        collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=None,
        )
    except Exception:
        return {
            "total_count": 0,
            "filtered_count": 0,
            "documents": [],
            "collection_metadata": {"name": COLLECTION_NAME},
            "error": f"コレクション '{COLLECTION_NAME}' が見つかりません。データを初期化してください。",
        }

    # コレクションのメタ情報を取得
    collection_info = {
        "name": collection.name,
        "count": collection.count(),
    }
    if collection.metadata:
        collection_info["metadata"] = collection.metadata

    total_count = collection.count()

    # ── メタデータフィルタを構築 ──
    where_clauses = []
    if tenant_id:
        where_clauses.append({"tenant_id": {"$eq": tenant_id}})
    if doc_type:
        where_clauses.append({"doc_type": {"$eq": doc_type}})
    if effective_year:
        where_clauses.append({"effective_year": {"$gte": effective_year}})

    where = None
    if where_clauses:
        if len(where_clauses) == 1:
            where = where_clauses[0]
        else:
            where = {"$and": where_clauses}

    # ── ChromaDB からドキュメントを取得 ──
    raw = collection.get(
        where=where,
        offset=offset,
        limit=limit,
    )

    ids_list = raw.get("ids", [])
    docs_list = raw.get("documents", [])
    metas_list = raw.get("metadatas", [])

    # ── ドキュメントを整形 ──
    all_documents = []
    for i in range(len(ids_list)):
        meta = metas_list[i] if i < len(metas_list) else {}
        doc_entry = {
            "id": ids_list[i],
            "title": meta.get("title", ""),
            "tenant_id": meta.get("tenant_id", ""),
            "doc_type": meta.get("doc_type", ""),
            "effective_year": meta.get("effective_year"),
            "text": docs_list[i] if i < len(docs_list) else "",
        }
        all_documents.append(doc_entry)

    # ── テキスト検索で二次フィルタ ──
    if search_text:
        lower_search = search_text.lower()
        all_documents = [
            d for d in all_documents
            if lower_search in d.get("title", "").lower()
            or lower_search in d.get("text", "").lower()
        ]

    # ── フィルタ後の件数を取得（検索用） ──
    # ChromaDB の count() は where 引数を受け付けないので、全件取得してカウント
    if search_text:
        filtered_count = len(all_documents)
    else:
        count_raw = collection.get(where=where)
        filtered_count = len(count_raw.get("ids", []))

    return {
        "total_count": total_count,
        "filtered_count": filtered_count,
        "documents": all_documents,
        "collection_metadata": collection_info,
    }


def get_unique_values() -> dict:
    """
    コレクション内のメタデータユニーク値を取得する。

    戻り値: {
        "tenant_ids": [...],
        "doc_types": [...],
        "effective_years": [...],
    }
    """
    client = get_chroma_client()

    try:
        collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=None,
        )
    except Exception:
        return {
            "tenant_ids": [],
            "doc_types": [],
            "effective_years": [],
        }

    raw = collection.get()
    metas_list = raw.get("metadatas", [])

    tenant_ids = sorted(set(
        m.get("tenant_id") for m in metas_list if m.get("tenant_id")
    ))
    doc_types = sorted(set(
        m.get("doc_type") for m in metas_list if m.get("doc_type")
    ))
    effective_years = sorted(set(
        m.get("effective_year") for m in metas_list if m.get("effective_year") is not None
    ), reverse=True)

    return {
        "tenant_ids": tenant_ids,
        "doc_types": doc_types,
        "effective_years": effective_years,
    }
