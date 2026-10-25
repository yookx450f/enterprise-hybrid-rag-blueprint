"""
マルチテナント不動産管理SaaSの初期データ投入モジュール

ChromaDBコレクション「b2b_tenant_rules」に以下のダミーデータを投入する：
- 審査規約・トラブル事例（正解＋ノイズ混入）
- 店舗エスカレーションメール（日次要約バッチ用）
"""

import httpx
import chromadb
from config import settings

COLLECTION_NAME = "b2b_tenant_rules"


def get_chroma_client():
    """ChromaDBクライアントを返す"""
    return chromadb.HttpClient(
        host=settings.CHROMA_HOST,
        port=settings.CHROMA_PORT
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
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


def get_seed_documents() -> list[dict]:
    """投入するドキュメント一覧を返す"""
    documents = [
        # ===== 正解データ（Hybrid RAG が的確に抽出） =====
        {
            "id": "doc_001",
            "document": "【2026年最新改定】A社 フリーランス・個人事業主向け入居審査特例ルール：ITエンジニア等のフリーランスは、確定申告書が1期分のみでも、直近3ヶ月の継続的な業務委託基本契約書（月額報酬が家賃の3.5倍以上）および預金残高（家賃12ヶ月分以上）の提出により、連帯保証人免除（保証会社Cプラン必須）で即時承認可能とする。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "審査規約",
                "effective_year": 2026,
                "title": "フリーランス・個人事業主向け入居審査特例ルール"
            }
        },
        {
            "id": "doc_002",
            "document": "【審査トラブル注意喚起（A社）】フリーランス審査特例において、単発スポット案件の請求書を『継続的な業務委託契約書』と誤認して承認し、半年後に家賃滞納が発生した事例あり。必ず契約期間3ヶ月以上の基本契約書＋個別注文書のセットを確認すること。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "トラブル事例",
                "effective_year": 2026,
                "title": "フリーランス審査特例における滞納トラブル注意喚起"
            }
        },
        {
            "id": "doc_003",
            "document": "【2026年度改定】A社 入居者年収基準および家賃割安率ガイドライン：原則として年収は家賃の40倍以上（月収は家賃の3.5倍以上）を必須条件とする。ただし、フリーランス特例適用者は業務委託契約書の累計月額収入をもって月収とみなし評価可能。連帯保証人の所得条件是不要となる。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "審査規約",
                "effective_year": 2026,
                "title": "入居者年収基準および家賃割安率ガイドライン"
            }
        },

        # ===== ノイズデータ（Naive RAG が誤って拾う罠データ） =====
        {
            "id": "doc_004",
            "document": "B社におけるフリーランス・個人事業主の入居審査基準：原則として連帯保証人2名と信販系保証会社の必須加入を求め、確定申告書3年分の提出がない場合は審査不可とする。",
            "metadata": {
                "tenant_id": "B社_全国賃貸",
                "doc_type": "審査規約",
                "effective_year": 2026,
                "title": "フリーランス入居審査特例（信販系保証必須）"
            }
        },
        {
            "id": "doc_005",
            "document": "【2022年旧規定・廃止】A社における個人事業主・フリーランスの審査基準：開業3年未満の個人事業主は原則審査お断り。預金残高による特例審査も一切不可とする。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "審査規約",
                "effective_year": 2022,
                "title": "【廃止済】旧個人事業主審査基準"
            }
        },

        # ===== その他業務データ（A社・B社・C社） =====
        {
            "id": "doc_006",
            "document": "【C社】フリーランス契約社員の法人契約特例について：委託先の正社員と同様に入居審査を行い、個人での債務残高が1,000万円未満の場合は連帯保証人不要とする。ただし毎月の家賃は会社からの給与振込必須。",
            "metadata": {
                "tenant_id": "C社_社宅代行",
                "doc_type": "審査規約",
                "effective_year": 2026,
                "title": "フリーランス契約社員向け法人契約特例"
            }
        },
        {
            "id": "doc_007",
            "document": "【A社】退去時の原状回復基準：入居者負担範囲は①キズ・破損の修理代②特注什器撤去費③専門的クリーニング費用。自然経年劣化（壁紙の薄い日焼け程度、床の軽いツブレ）は入居者が負担しないことを明記。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "管理規約",
                "effective_year": 2026,
                "title": "退去時の原状回復基準"
            }
        },
        {
            "id": "doc_008",
            "document": "【A社】入居者向け修繕費申請手順：家屋内設備の不具合発生時は管理ポータルから24時間以内にエスカレーション登録必須。応急処置が必要な場合は緊急連絡先（0120-XXXX-XX）へ電話後、システム追記を行うこと。修繕対応は原則7営業日以内完了。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "運用ガイドライン",
                "effective_year": 2026,
                "title": "修繕費申請手順"
            }
        },
        {
            "id": "doc_009",
            "document": "【B社】物件回遊ルール：他都市の同物件への引越希望時は、現入居期間が6ヶ月以上であることと滞納履歴なしを条件に優先配置権を付与。引越し費用のうち新築補償分（1万円）を補助可能。",
            "metadata": {
                "tenant_id": "B社_全国賃貸",
                "doc_type": "管理規約",
                "effective_year": 2026,
                "title": "物件回遊ルール"
            }
        },
        {
            "id": "doc_010",
            "document": "【C社】法人向け定期点検制度：入居後3ヶ月・6ヶ月・12ヶ月で定期点検を実施。このタイミングでの設備不具合は修繕費無料適用（工事会社選定はC社委託先）。",
            "metadata": {
                "tenant_id": "C社_社宅代行",
                "doc_type": "管理規約",
                "effective_year": 2026,
                "title": "法人向け定期点検制度"
            }
        },

        # ===== 店舗エスカレーションメール（日次要約バッチ用） =====
        {
            "id": "doc_011",
            "document": "【渋谷支店より】本入居予定のフリーランスエンジニアの方について：業務委託契約書は月額55万円だが、契約期間が2ヶ月で更新見込みとのこと。A社特例ルールでは3ヶ月以上の基本契約書が必要ですが、2ヶ月+更新意向書のセットでも承認可能でしょうか？来週の入居締切までにご回答ください。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "エスカレーションメール",
                "effective_year": 2026,
                "title": "渋谷支店-フリーランス契約期間の適用確認"
            }
        },
        {
            "id": "doc_012",
            "document": "【新宿支店より】入居者からの苦情：専用部エアコンが動かなくなっており、修繕申請を5日前にポータル登録済みですが未対応です。お客様は「契約書では7営業日以内」と主張していますが、実際には9営業日経過しています。速やかな対応をお願いします。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "エスカレーションメール",
                "effective_year": 2026,
                "title": "新宿支店-修繕対応遅延による入居者苦情"
            }
        },
        {
            "id": "doc_013",
            "document": "【横浜支店より】退去手続き中のお客様から「壁紙の薄い黄ばみは経年劣化だから負担したくない」との主張あり。A社管理規約では「薄い日焼け程度」が入居者不要範囲と明記されていますが、今回の黄ばみの程度判断基準について法務担当からの見解をお願いします。退去予定日は今月末です。",
            "metadata": {
                "tenant_id": "A社_都市型",
                "doc_type": "エスカレーションメール",
                "effective_year": 2026,
                "title": "横浜支店-退去原状回復の黄ばみ判断基準"
            }
        },
        {
            "id": "doc_014",
            "document": "【埼玉支店より】B社物件に入居希望の個人事業主から、確定申告書は直近1年分しか作成していないとの連絡あり。B社ルールでは3年分必須と理解していますが、例外的な緩和条件はありますか？審査期限が今月中のため急ぎ対応ください。",
            "metadata": {
                "tenant_id": "B社_全国賃貸",
                "doc_type": "エスカレーションメール",
                "effective_year": 2026,
                "title": "埼玉支店-確定申告書年数の緩和相談"
            }
        },
        {
            "id": "doc_015",
            "document": "【千葉支店より】C社法人契約のお客様（従業員15名）の定期点検日程調整について：次回点検は入居から6ヶ月目にあたりますが、来月は法人事業部の連休に重なり担当者が不在です。翌月へ延期可能か、また延期した場合の無料修繕適用期限に影響があるかお伺いください。",
            "metadata": {
                "tenant_id": "C社_社宅代行",
                "doc_type": "エスカレーションメール",
                "effective_year": 2026,
                "title": "千葉支店-定期点検日程延期の相談"
            }
        },
    ]
    return documents


async def seed_all_data() -> dict:
    """
    ChromaDBへの初期データ投入を実行する。
    Ollama Embedding API でベクトル化后、既にドキュメントが存在する場合は上書き（一旦削除後に再投入）。
    """
    client = get_chroma_client()

    # コレクションの作成（または取得）
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

    docs = get_seed_documents()
    ids = [d["id"] for d in docs]
    texts = [d["document"] for d in docs]
    metadatas = [d["metadata"] for d in docs]

    # Ollama でバッチエンベッディング
    embeddings = await embed_texts(texts)

    collection.add(
        ids=ids,
        documents=texts,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    return {
        "status": "ok",
        "collection": COLLECTION_NAME,
        "documents_inserted": len(docs),
    }
