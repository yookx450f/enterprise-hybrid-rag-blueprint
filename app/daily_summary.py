"""
日次バッチ要約機能
Phase 3: 店舗からのエスカレーションメールを一括解析し、緊急度・要約・推奨アクションを返す。
"""

import time
import httpx
from config import settings


# ─── エスカレーションメール固定リスト ──────────────────────

ESCALATION_EMAILS = [
    {
        "id": "email_001",
        "source": "渋谷支店",
        "body": (
            "本入居予定のフリーランスエンジニアの方について：業務委託契約書は月額55万円だが、"
            "契約期間が2ヶ月で更新見込みとのこと。A社特例ルールでは3ヶ月以上の基本契約書が必要ですが、"
            "2ヶ月+更新意向書のセットでも承認可能でしょうか？来週の入居締切までにご回答ください。"
        ),
    },
    {
        "id": "email_002",
        "source": "新宿支店",
        "body": (
            "入居者からの苦情：専用部エアコンが動かなくなっており、修繕申請を5日前にポータル登録済みですが未対応です。"
            "お客様は「契約書では7営業日以内」と主張していますが、実際には9営業日経過しています。"
            "速やかな対応をお願いします。"
        ),
    },
    {
        "id": "email_003",
        "source": "横浜支店",
        "body": (
            "退去手続き中のお客様から「壁紙の薄い黄ばみは経年劣化だから負担したくない」との主張あり。"
            "A社管理規約では「薄い日焼け程度」が入居者不要範囲と明記されていますが、"
            "今回の黄ばみの程度判断基準について法務担当からの見解をお願いします。退去予定日は今月末です。"
        ),
    },
    {
        "id": "email_004",
        "source": "埼玉支店",
        "body": (
            "B社物件に入居希望の個人事業主から、確定申告書は直近1年分しか作成していないとの連絡あり。"
            "B社ルールでは3年分必須と理解していますが、例外的な緩和条件はありますか？"
            "審査期限が今月中のため急ぎ対応ください。"
        ),
    },
    {
        "id": "email_005",
        "source": "千葉支店",
        "body": (
            "C社法人契約のお客様（従業員15名）の定期点検日程調整について：次回点検は入居から6ヶ月目にあたりますが、"
            "来月は法人事業部の連休に重なり担当者が不在です。翌月へ延期可能か、"
            "また延期した場合の無料修繕適用期限に影響があるかお伺いください。"
        ),
    },
]


async def run_daily_summary(llm_model: str | None = None) -> dict:
    """
    店舗からのエスカレーションメール5件を一括解析する。

    LLMに全てのメール本文を送信し、各メールについて以下の情報をJSON配列で返す：
    - 緊急度（高/中/低）
    - テナント名（関連する会社・支店）
    - 1行要約
    - 推奨アクション
    """
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    # メール本文をまとめてプロンプトに組み込む
    email_lines = []
    for i, email in enumerate(ESCALATION_EMAILS, start=1):
        email_lines.append(
            f"[メール{i}（{email['source']}）]\n{email['body']}"
        )

    emails_block = "\n\n".join(email_lines)

    prompt = (
        "あなたは不動産管理SaaSの業務アシスタントです。"
        "以下の各店舗から届いたエスカレーションメールを解析し、"
        "各メールについて「緊急度」「テナント名（支店）」「1行要約」「推奨アクション」を判定してください。\n\n"
        f"=== メール一覧 ===\n{emails_block}\n\n"
        "=== 回答フォーマット ===\n"
        "以下のJSON配列形式で回答してください：\n"
        "[\n"
        '  {\n'
        '    "mail_id": "メール番号",\n'
        '    "urgency": "高 または 中 または 低",\n'
        '    "tenant": "関連する支店名",\n'
        '    "summary": "1行要約",\n'
        '    "recommended_action": "推奨アクション",\n'
        '  },\n'
        "  ...\n"
        "]\n\n"
        "JSON配列のみを回答してください。他の説明や前置きは不要です。"
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
    answer = data.get("response", "")

    return {
        "answer": answer,
        "emails_count": len(ESCALATION_EMAILS),
        "inference_time_ms": inference_time_ms,
        "prompt_length": len(prompt),
    }
