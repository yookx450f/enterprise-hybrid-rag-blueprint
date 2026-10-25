# ストリーミング検索過程表示 — 実装計画

## 概要

現在の比較検索（`/api/compare_rag`）は完了後に一括で結果を返す。これを**各パイプラインステップをリアルタイムにストリーミング**して表示する仕組みに変更する。

---

## アーキテクチャ全体図

```
ブラウザ                    FastAPI                    RAGエンジン
┌──────────┐               ┌───────────┐              ┌──────────────┐
│ fetch +  │ POST          │ /api/     │ asyncio.gather │ stream_      │
│ Readable │──────────────▶│ compare_  │◀─────────────▶│ naive_rag()  │
│ Stream   │               │ rag_stream│               │              │
│          │◀── SSE ──────▶│           │◀─────────────▶│ stream_      │
│          │               │           │               │ hybrid_rag() │
└──────────┘               └───────────┘              └──────────────┘
    │                              │
    ▼                              ▼
EventDispatcher            MultiplexStream
・step_start           ・両エンジンのyieldを
・step_done            ・engineタグで区別し
・chunk_preview        ・合并してSSE送信
・answer               ・JSON配列として
・done                  
```

---

## 1. SSEイベント形式の設計

### イベントプロトコル

`fetch + ReadableStream` を使い、レスポンスボディを `text/plain; text/event-stream` として受信する。各行は以下の形式：

```
data: {"type": "step_start", "engine": "naive", "step_id": "embed", "name": "クエリベクトル化", "ts": 1729876543}
data: {"type": "step_done", "engine": "naive", "step_id": "embed", "duration_ms": 342, "detail": "モデル: bge-m3"}
data: {"type": "chunk_preview", "engine": "hybrid", "chunks": [{"title":"...","text_preview":"...100字...","score":0.95}]}
data: {"type": "answer", "engine": "naive", "answer": "回答テキスト...", "inference_time_ms": 4500}
data: {"type": "done", "engine": "hybrid"}
```

**型定義**:
| type | 説明 | engine | 必須フィールド |
|------|------|--------|---------------|
| `step_start` | ステップ開始 | `"naive"` \| `"hybrid"` | `step_id`, `name` |
| `step_done` | ステップ完了 | `"naive"` \| `"hybrid"` | `step_id`, `duration_ms`, `detail?` |
| `chunk_preview` | チャンクプレビュー | `"naive"` \| `"hybrid"` | `chunks[]` |
| `answer` | 最終回答 | `"naive"` \| `"hybrid"` | `answer`, `inference_time_ms` |
| `done` | エンジン完了 | `"naive"` \| `"hybrid"` | - |

---

## 2. バックエンド変更設計

### 2.1 `app/rag_engine.py` — ストリーミングジェネレーター追加

**既存の `run_naive_rag()` はそのまま残す**（後方互換性）。新しい `stream_naive_rag()` を追加：

```python
async def stream_naive_rag(
    query: str,
    llm_model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """各ステップ完了時にyieldするストリーミング版Naive RAG"""
    
    yield {"type": "step_start", "step_id": "embed", "name": "クエリベクトル化"}
    # → embed処理...
    yield {"type": "step_done", "step_id": "embed", "duration_ms": XXX}
    
    yield {"type": "step_start", "step_id": "vector_search", "name": "ベクトル検索"}
    # → 検索処理...
    yield {"type": "step_done", "step_id": "vector_search", "duration_ms": XXX}
    
    # チャンクプレビュー送信（タイトル + 100字本文プレビュー + スコア）
    yield {
        "type": "chunk_preview",
        "chunks": [
            {
                "id": chunk_id,
                "title": meta.get("title", ""),
                "text_preview": doc_text[:100] + "...",
                "distance": distance_val,
            }
            for chunk_id, doc_text, meta, distance_val in zip(...)
        ]
    }
    
    yield {"type": "step_start", "step_id": "llm", "name": "LLM推論"}
    # → LLM呼び出し...
    yield {
        "type": "answer",
        "answer": llm_response,
        "inference_time_ms": XXX
    }
    yield {"type": "done"}
```

### 2.2 `app/search_engine.py` — ストリーミングジェネレーター追加

同じく **既存の `run_hybrid_rag()` はそのまま**。新しい `stream_hybrid_rag()` を追加：

```python
async def stream_hybrid_rag(
    query: str,
    tenant_id: str = "A社_都市型",
    min_year: int = 2025,
    llm_model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """各ステップ完了時にyieldするストリーミング版Hybrid RAG"""
    
    yield {"type": "step_start", "step_id": "embed", "name": "クエリベクトル化"}
    # → embed処理...
    yield {"type": "step_done", "step_id": "embed", "duration_ms": XXX}
    
    yield {"type": "step_start", "step_id": "meta_filter", "name": "メタデータフィルタ"}
    # → メタフィルタ + ベクトル検索...
    yield {"type": "step_done", "step_id": "meta_filter", "duration_ms": XXX, 
           "count_before": total, "count_after": filtered}
    
    yield {"type": "step_start", "step_id": "bm25", "name": "BM25スコアリング"}
    # → BM25処理...
    yield {"type": "step_done", "step_id": "bm25", "duration_ms": XXX}
    
    yield {"type": "step_start", "step_id": "rerank", "name": "RRFリランキング"}
    # → RRF処理...
    yield {"type": "step_done", "step_id": "rerank", "duration_ms": XXX,
           "count_before": N, "count_after": 3}
    
    # チャンクプレビュー（RRFスコア付き）
    yield {
        "type": "chunk_preview",
        "chunks": [
            {
                "id": doc_id,
                "title": title,
                "text_preview": text[:100] + "...",
                "vector_rank": vec_rank,
                "bm25_rank": bm25_rank,
                "rrf_score": rrf_val,
            }
            for ...
        ]
    }
    
    yield {"type": "step_start", "step_id": "llm", "name": "LLM推論"}
    # → LLM呼び出し...
    yield {
        "type": "answer",
        "answer": llm_response,
        "inference_time_ms": XXX
    }
    yield {"type": "done"}
```

### 2.3 `app/main.py` — SSEエンドポイント追加

```python
from fastapi.responses import StreamingResponse

@app.post("/api/compare_rag_stream")
async def compare_rag_stream(req: CompareRAGRequest):
    """
    ストリーミング版比較API。
    両エンジンのパイプライン進行をSSEでリアルタイム送信。
    """
    async def event_generator():
        naive_gen = stream_naive_rag(query=req.query, llm_model=req.llm_model)
        hybrid_gen = stream_hybrid_rag(
            query=req.query, tenant_id=req.tenant_id,
            min_year=req.min_year, llm_model=req.llm_model,
        )
        
        # 両方のジェネレーターを並列実行
        # asyncio.gatherではなく、aiozipのような逐次yieldが必要
        naive_q: asyncio.Queue = asyncio.Queue()
        hybrid_q: asyncio.Queue = asyncio.Queue()
        
        async def fill_queue(gen, queue, engine_tag):
            async for event in gen:
                event["engine"] = engine_tag
                await queue.put(event)
            await queue.put(None)  # 終端マーカー
        
        asyncio.create_task(fill_queue(naive_gen, naive_q, "naive"))
        asyncio.create_task(fill_queue(hybrid_gen, hybrid_q, "hybrid"))
        
        done_naive = False
        done_hybrid = False
        while not (done_naive and done_hybrid):
            # 両キューからイベントを消费
            ...
            event_str = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield event_str
        
        # 全完了
        yield 'data: {"type": "all_done"}\n\n'
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

**重要な実装パターン**: 両エンジンの並列実行には `asyncio.Queue` を使い、各ジェネレーターを別タスクで走らせる。メインループはキューからイベントを取り出して SSE形式で yield する。

---

## 3. フロントエンド変更設計

### 3.1 `app/static/js/dashboard.js` — SSE受信ロジック

新しい関数 `runCompareStream()` を追加：

```javascript
async function runCompareStream() {
    const query = document.getElementById('query-input').value.trim();
    if (!query) { showToast('質問文を入力してください', 'error'); return; }

    // 初期化: 両パネルを空に
    const naiveBody = document.getElementById('naive-body');
    const hybridBody = document.getElementById('hybrid-body');
    naiveBody.innerHTML = '';
    hybridBody.innerHTML = '';

    // パイプラインコンテナを事前に描画（空）
    renderEmptyPipeline(naiveBody, 'naive');
    renderEmptyPipeline(hybridBody, 'hybrid');

    try {
        const response = await fetch('/api/compare_rag_stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                query,
                tenant_id: document.getElementById('tenant-select').value,
                min_year: parseInt(document.getElementById('year-select').value),
                llm_model: getSelectedModel(),
            }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let naiveDone = false, hybridDone = false;

        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, {stream: true});
            const lines = buffer.split('\n');
            buffer = lines.pop(); // 未完成行は保持
            
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = JSON.parse(line.slice(6));
                handleSSEEvent(data, naiveBody, hybridBody);
            }
        }
    } catch (e) {
        console.error('SSEエラー', e);
        showToast('ストリーミング検索に失敗しました', 'error');
    }
}

function handleSSEEvent(event, naiveBody, hybridBody) {
    const target = event.engine === 'naive' ? naiveBody : hybridBody;
    
    switch (event.type) {
        case 'step_start':
            // 「処理中」アニメーション付きのステップ行を追加
            addStepRow(target, event, 'processing');
            break;
        case 'step_done':
            // 処理中行 → 完了行に置換
            updateStepRow(event.engine, event.step_id, 'done', event);
            break;
        case 'chunk_preview':
            // チャンクプレビューアコーディオンを描画
            renderChunkPreviewAccordion(target, event.chunks, event.engine);
            break;
        case 'answer':
            // 回答エリアを描画 + メタ情報更新
            renderAnswerArea(target, event.answer, event.inference_time_ms, event.engine);
            const metaId = event.engine === 'naive' ? 'naive-meta' : 'hybrid-meta';
            document.getElementById(metaId).textContent = 
                `推論 ${event.inference_time_ms}ms`;
            break;
        case 'done':
            // エンジン完了マーク
            markEngineDone(event.engine);
            break;
        case 'all_done':
            showToast('比較検索完了');
            break;
    }
}
```

### 3.2 チャンクプレビューアコーディオン

`renderChunkPreviewAccordion(container, chunks, engine)` で展開/收起可能なUIを描画：

```javascript
function renderChunkPreviewAccordion(container, chunks, engine) {
    const isNaive = engine === 'naive';
    let html = '<div class="mt-3 space-y-2">';
    html += `<p class="text-xs font-semibold text-slate-500">参照チャンク (${chunks.length}件):</p>`;
    
    chunks.forEach((c, i) => {
        const borderClass = isNaive ? 'border-red-800/30' : 'border-emerald-800/30';
        const badgeClass = isNaive ? 'bg-red-900 text-red-300' : 'bg-emerald-900 text-emerald-300';
        
        html += `
        <div class="chunk-preview-card bg-slate-950 border ${borderClass} rounded-lg overflow-hidden">
            <!-- ヘッダー（クリックで展開） -->
            <div class="px-3 py-2 flex items-center gap-2 cursor-pointer hover:bg-slate-800/50"
                 onclick="toggleChunkPreview('chunk-${engine}-${i}')">
                <span class="chunk-badge ${badgeClass}">${escapeHtml(c.title || '無題')}</span>
                <span class="text-[10px] text-slate-500">distance: ${c.distance ?? '-'}</span>
                ${c.rrf_score ? `<span class="chunk-badge bg-amber-900 text-amber-300">rrf=${c.rrf_score}</span>` : ''}
                <svg class="chunk-arrow w-3 h-3 ml-auto text-slate-600" id="arrow-chunk-${engine}-${i}">...</svg>
            </div>
            <!-- 展開コンテンツ -->
            <div id="chunk-preview-chunk-${engine}-${i}" class="hidden border-t border-slate-800/50">
                <div class="px-3 py-2 text-xs text-slate-400 leading-relaxed whitespace-pre-wrap">
                    ${escapeHtml(c.text_preview)}
                </div>
            </div>
        </div>`;
    });
    
    html += '</div>';
    
    // 既存のチャンクリストを置換、または追加
    let listEl = container.querySelector('.chunk-preview-list');
    if (listEl) {
        listEl.innerHTML = html;
    } else {
        const div = document.createElement('div');
        div.className = 'chunk-preview-list';
        div.innerHTML = html;
        container.appendChild(div);
    }
}

window.toggleChunkPreview = function(id) {
    const content = document.getElementById(`chunk-preview-${id}`);
    const arrow = document.getElementById(`arrow-${id}`);
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        arrow.style.transform = 'rotate(180deg)';
    } else {
        content.classList.add('hidden');
        arrow.style.transform = '';
    }
};
```

### 3.3 HTML変更 — `app/templates/index.html`

**最小限の変更**: 比較実行ボタンにストリーミングモードの切り替えを追加（または既存を置き換え）：

```html
<button id="compare-btn" class="bg-emerald-600 hover:bg-emerald-500 ...">
    <span>🚀 比較実行（ストリーミング）</span>
</button>
```

JS側では `initCompareTab()` でリスナーを `runCompareStream` に変更：
```javascript
function initCompareTab() {
    document.getElementById('compare-btn').addEventListener('click', runCompareStream);
}
```

---

## 4. CSS変更 — `app/static/css/dashboard.css`

### 4.1 「処理中」アニメーション

```css
/* 処理中のステップ行 */
.pipeline-step-row.status-processing {
    border-left-color: #3b82f6;
    background: rgba(59, 130, 246, 0.08);
    animation: bluePulse 1.5s ease-in-out infinite;
}

@keyframes bluePulse {
    0%, 100% {
        background: rgba(59, 130, 246, 0.08);
        border-left-color: #3b82f6;
    }
    50% {
        background: rgba(59, 130, 246, 0.18);
        border-left-color: #60a5fa;
    }
}

/* 処理中アイコン */
.status-processing .pipeline-icon {
    background: rgba(59, 130, 246, 0.2);
    color: #60a5fa;
    animation: processingSpin 1s linear infinite;
}

@keyframes processingSpin {
    to { transform: rotate(360deg); }
}

/* チャンクプレビューカード */
.chunk-preview-card {
    transition: all 0.2s ease;
}

.chunk-preview-card:hover {
    border-color: rgba(100, 116, 139, 0.5) !important;
}

.chunk-arrow {
    transition: transform 0.2s ease;
}
```

---

## 5. 変更ファイル一覧

| ファイル | 変更内容 | 行数見積もり |
|----------|---------|-------------|
| `app/rag_engine.py` | `stream_naive_rag()` 追加 | +80行 |
| `app/search_engine.py` | `stream_hybrid_rag()` 追加 | +90行 |
| `app/main.py` | `/api/compare_rag_stream` + Queueベースのマルチプレクサ | +70行 |
| `app/static/js/dashboard.js` | SSE受信ロジック + チャンクプレビュー | +120行 |
| `app/static/css/dashboard.css` | 処理中アニメーション | +35行 |
| `app/templates/index.html` | ボタンラベル変更（最小限） | +2行 |

---

## 6. 実装順序

1. **RAGエンジン改造** — `stream_naive_rag()` / `stream_hybrid_rag()` を追加
2. **SSEエンドポイント** — `/api/compare_rag_stream` を実装
3. **CSSアニメーション** — `.status-processing` クラスを追加
4. **フロントエンドJS** — SSE受信 + DOM逐次描画ロジック
5. **HTML微修正** — ボタンラベル更新
6. **テスト確認** — 両エンジンの並列ストリーミングをブラウザで検証

---

## 7. リスクと注意点

- **Nginx/リバースプロキシ**: SSEは長寿命接続なので、プロキシのバッファリング無効（`X-Accel-Buffering: no`）が必要
- **タイムアウト**: uvicornのデフォルトリクエストタイムアウトが長い場合は問題ないが、本番では `timeout_keep_alive` を調整
- **既存API後方互換**: `/api/compare_rag` はそのまま残す
- **エラーハンドリング**: ジェネレーター内で例外発生時は `{"type": "error", "message": "..."}` を送信し、クリーンアップ
