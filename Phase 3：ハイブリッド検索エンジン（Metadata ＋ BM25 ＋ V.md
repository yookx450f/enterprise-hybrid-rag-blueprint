Phase 3：ハイブリッド検索エンジン（Metadata ＋ BM25 ＋ Vector RRF）の実装
ここからがこのデモの核心です。Phase 2で作った rag_engine.py に**「メタデータ事前絞り込み ＋ Janome形態素解析 ＋ BM25キーワード検索 ＋ RRF統合リランキング」**を追加し、Naive RAGとの劇的な精度差をAPIレベルで完成させます。
3-1. ZOO CODE 投入用プロンプト（Phase 3）
【Phase 3：ハイブリッド検索（Metadata + Janome BM25 + Vector RRF）と比較APIの実装】

Phase 2 のデータ投入と Naive RAG が正常に動作しました。

続いて Phase 3 として、`app/rag_engine.py` にハイブリッド検索エンジンを追加し、Naive RAG と Hybrid RAG の結果を同時比較できるエンドポイントを実装してください。

## Phase 3 のスコープと仕様

1. `app/rag_engine.py` の拡張（`run_hybrid_rag` の実装）

   - 引数として `query`, `tenant_id`（例: `"A社_都市型"`）, `min_year`（例: `2025`）, `llm_model` を受け取る関数 `run_hybrid_rag()` を実装してください。

   - **Step 1（Metadata Pre-filtering）：** ChromaDB検索時に `where={"$and": [{"tenant_id": {"$eq": tenant_id}}, {"effective_year": {"$gte": min_year}}]}` を適用し、他社規約や廃止された旧規定を構造的に排除して候補チャンクを取得する。

   - **Step 2（日本語形態素解析 ＋ BM25 スコアリング）：**

     - `janome.tokenizer.Tokenizer` を使って、絞り込まれた候補チャンクおよび質問文から名詞・動詞・形容詞（自立語）のトークンリストを抽出する関数を作成。

     - `rank_bm25.BM25Okapi` を用いて、質問文に対する各候補チャンクのBM25キーワードスコアを算出する。

   - **Step 3（RRF: Reciprocal Rank Fusion による順位統合）：**

     - ベクトル検索の順位（`rank_vec`）と BM25 の順位（`rank_bm25`）を、`rrf_score = 1 / (60 + rank_vec) + 1 / (60 + rank_bm25)` で統合してリランキングし、上位3件のチャンクを最終コンテキストとして選定する。

   - **Step 4（構造化プロンプトによるLLM回答生成）：**

     - 選定された正確なチャンクのみをコンテキストとして渡し、「①審査可否の結論、②特例適用の必須条件、③過去トラブル事例を踏まえた審査時の注意点、④根拠とした規定タイトル」の4項目で明快に回答を生成させる。

   - 戻り値には「回答」「選定されたチャンク一覧（ベクトル順位・BM25順位・RRFスコア付き）」「プロンプト文字数」「推論時間（ms）」を含めること。

2. 日次バッチ要約機能（`run_daily_summary`）の追加

   - `app/seed_data.py` に「本日の各店舗からのエスカレーション・審査例外相談メール（5件）」の固定リストを用意し、それらをLLMで一括解析して各メールの「緊急度（高/中/低）」「テナント名」「1行要約」「推奨アクション」をJSON配列（またはMarkdown表）で返す関数を実装してください。

3. `app/main.py` への比較エンドポイント追加

   - `POST /api/compare_rag`: 1回のリクエストで `run_naive_rag` と `run_hybrid_rag` を実行し、両方の結果を並べて返すAPI。

   - `POST /api/daily_summary`: 店舗メール5件の一括要約・分類結果を返すAPI。
3-2. Phase 3 の動作確認ゲート
再び http://localhost:8080/docs を開き、POST /api/compare_rag をテスト実行します。
成功条件： JSONレスポンスの中で、naive_rag 側は他社・旧規定を拾ってノイズが混ざっているのに対し、hybrid_rag 側は「A社・2026年最新規約」と「A社・過去トラブル事例」の2つだけをRRFスコア上位で的確に射抜き、完璧な回答が返ってくれば Phase 3 クリアです！
