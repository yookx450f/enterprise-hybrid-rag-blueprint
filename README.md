# enterprise-hybrid-rag-blueprint

エンタープライズ向けマルチテナント・ハイブリッドRAGのリファレンス実装です。

メタデータ絞り込み＋日本語BM25＋ベクトル検索をRRF（Reciprocal Rank Fusion）で統合し、Naive RAGとの差を明確にデモンストレーションするシステムです。不動産管理SaaSの業務シナリオを想定したサンプルデータとWebダッシュボードを同梱しています。

## 概要

本プロジェクトは、以下の4フェーズで構成されるRAGシステムの参考実装です。

| フェーズ | 内容 |
|----------|------|
| Phase 1 | ChromaDB ＆ Ollama のインフラ疎通確認 |
| Phase 2 | 業務データ投入 ＆ 単純ベクトル検索（Naive RAG） |
| Phase 3 | ハイブリッド検索エンジン（Metadata＋BM25＋Vector RRF統合） |
| Phase 4 | 検索結果比較用Webダッシュボード |

## アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│                  Web ダッシュボード                    │
│              (Jinja2 + Tailwind CSS)                 │
├─────────────────────────────────────────────────────┤
│                   FastAPI アプリ                      │
│  ┌──────────────┐  ┌─────────────────────────────┐  │
│  │  Naive RAG   │  │      Hybrid RAG Engine       │  │
│  │  (ベクトル)    │  │  ┌─────────────────────────┐ │  │
│  └──────┬───────┘  │  │ Metadata Pre-filtering    │ │  │
│         │          │  ├───────────────────────────┤ │  │
│         │          │  │ Janome 形態素解析 + BM25   │ │  │
│         │          │  ├───────────────────────────┤ │  │
│         │          │  │ RRF 統合リランキング       │ │  │
│         │          │  └──────────────┬────────────┘ │  │
│         │          └─────────────────┼──────────────┘  │
│         └────────────────────────────┼────────────────  │
│                        LLM (Ollama)  │                  │
│                        Embedding     │                  │
├──────────────────────────────────────┼──────────────────┤
│                 ChromaDB (ベクトルDB) │                  │
└──────────────────────────────────────┴──────────────────┘
```

## 技術スタック

| コンポーネント | テクノロジー |
|---------------|-------------|
| Webフレームワーク | FastAPI + Uvicorn |
| ベクトルDB | ChromaDB (cosine類似度) |
| LLM / Embedding | Ollama (ローカル推論) |
| 日本語形態素解析 | Janome |
| キーワード検索 | rank-bm25 (BM25Okapi) |
| テンプレートエンジン | Jinja2 |
| フロントエンド | Tailwind CSS (CDN) |
| コンテナ化 | Docker Compose |

## ハイブリッド検索の仕組み

Hybrid RAGは以下の4ステップで動作します。

1. **クエリベクトル化** — Ollama Embedding API でクエリをベクトル化
2. **メタデータプリフィルタリング** — テナントID・有効年度で候補を絞り込み
3. **BM25キーワードスコアリング** — Janomeで形態素解析した上でBM25Okapiによりキーワード関連性を算出
4. **RRF統合リランキング** — ベクトル検索順位とBM25順位をReciprocal Rank Fusionで融合し、上位3件を選定

これにより、Naive RAGでは「別テナントの罠データ」や「廃止済み規定」を誤ってヒットさせる問題を解消します。

## 起動方法

### 前提条件

- Docker & Docker Compose のインストール
- Ollama サーバーの稼働（`.env` で接続先を設定）
- Ollama にモデルのインストール:
  ```bash
  # LLM モデル
  ollama pull gemma4:31b
  # Embedding モデル
  ollama pull bge-m3
  ```

### 環境変数の設定

`.env` ファイルを編集してOllamaサーバーの接続先を設定します。

```env
OLLAMA_BASE_URL=http://100.126.109.125:11434
OLLAMA_HOST_M1MAX64GB=http://100.126.109.125:11434
OLLAMA_HOST_RTX3090=http://100.87.2.91:11434
LLM_MODEL=gemma4:31b
EMBED_MODEL=bge-m3
```

### 起動

```bash
docker compose up --build
```

- Webダッシュボード: `http://localhost:8080`
- ChromaDB: `http://localhost:8000`

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/health` | ChromaDB ＆ Ollama の疎通確認 |
| POST | `/api/seed` | サンプル業務データをChromaDBに投入 |
| POST | `/api/naive_rag` | 単純ベクトル検索（Naive RAG）実行 |
| POST | `/api/compare_rag` | Naive RAG と Hybrid RAG の結果を並列比較 |
| POST | `/api/compare_rag_stream` | ストリーミング版比較（SSE） |
| POST | `/api/daily_summary` | 店舗メール5件の一括要約・分類 |
| GET | `/api/collection/browser` | コレクション内のドキュメント一覧取得 |
| GET | `/api/collection/values` | メタデータユニーク値一覧（フィルタ用） |
| GET | `/api/debug/collection` | デバッグ用コレクション内容確認 |

## サンプルデータ

不動産管理SaaSのマルチテナント環境を想定した15件のドキュメントを同梱しています。

| カテゴリ | 件数 | 説明 |
|---------|------|------|
| 正解データ | 3件 | A社のフリーランス審査特例ルール・トラブル事例・年収基準 |
| ノイズデータ | 2件 | B社の厳格な審査基準・A社の廃止済み旧規定（Naive RAGの罠） |
| 業務データ | 5件 | A社〜C社の管理規約・運用ガイドライン |
| エスカレーションメール | 5件 | 各支店からの相談メール（日次要約バッチ用） |

## プロジェクト構造

```
├── docker-compose.yml          # ChromaDB + FastAPI コンテナ定義
├── .env                        # 環境変数設定
├── app/
│   ├── main.py                # FastAPI アプリケーション（ルート・APIエンドポイント）
│   ├── config.py              # 環境設定読み込み（Pydantic Settings）
│   ├── rag_engine.py          # Naive RAG エンジン（単純ベクトル検索）
│   ├── search_engine.py       # Hybrid RAG エンジン（Metadata+BM25+Vector RRF）
│   ├── seed_data.py           # サンプルデータ投入モジュール
│   ├── daily_summary.py       # 日次バッチ要約機能
│   ├── collection_browser.py  # コレクションブラウザ用ヘルパー
│   ├── Dockerfile             # アプリコンテナ定義
│   ├── requirements.txt       # Python依存ライブラリ
│   ├── templates/
│   │   └── index.html         # Webダッシュボードテンプレート
│   └── static/
│       ├── css/dashboard.css  # ダッシュボードスタイル
│       └── js/dashboard.js    # ダッシュボードJavaScript（SSE対応）
└── plans/
    └── debug_comparison_search.md  # デバッグ計画書
```

## ライセンス

MIT
