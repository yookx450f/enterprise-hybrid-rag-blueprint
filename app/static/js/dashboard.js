/**
 * Phase 4: ダッシュボード用JavaScript
 * API通信・タブ切り替え・結果表示のロジック
 */

// ===== ユーティリティ =====

function getSelectedModel() {
    const sel = document.getElementById('model-select');
    return sel.value || null;
}

function getSelectedHost() {
    const sel = document.getElementById('host-select');
    return sel.value || null;
}

function showToast(msg, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

async function apiCall(url, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
}

// Markdownレンダリング
function renderMarkdown(content) {
    if (typeof marked !== 'undefined' && marked.parse) {
        try {
            return marked.parse(content || '', { breaks: true, gfm: true });
        } catch (e) {
            console.warn('Markdownパース失敗', e);
        }
    }
    return escapeHtml(content || '');
}

// ===== 初期化処理 =====

document.addEventListener('DOMContentLoaded', () => {
    initHostSelector();
    initHealthCheck();
    initSeedButton();
    initTabs();
    initCompareTab();
    initSummaryTab();
    initBrowserTab();
});

// ===== LLMホストセレクトボックス =====

async function loadModelsForHost(host) {
    try {
        const url = host ? `/api/health?host=${encodeURIComponent(host)}` : '/api/health';
        const data = await apiCall(url);
        const modelSel = document.getElementById('model-select');
        
        if (data.available_models && data.available_models.length) {
            modelSel.innerHTML = '<option value="">モデルを選択...</option>';
            data.available_models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSel.appendChild(opt);
              });
              // デフォルトで completion 対応モデルを選ぶ
            const pref = data.available_models.find(m => !m.includes('bge') && !m.includes('embed'));
            if (pref) modelSel.value = pref;
        } else {
            modelSel.innerHTML = '<option value="">モデルがありません</option>';
        }
    } catch (e) {
        console.warn('モデル一覧取得失敗', e);
        const modelSel = document.getElementById('model-select');
        modelSel.innerHTML = '<option value="">エラー</option>';
    }
}

async function initHostSelector() {
    try {
        const data = await apiCall('/api/ollama/hosts');
        const hostSel = document.getElementById('host-select');
        hostSel.innerHTML = '<option value="">デフォルト</option>';
        for (const [alias, url] of Object.entries(data.hosts || {})) {
            const opt = document.createElement('option');
            opt.value = alias;
            const hostPort = url.replace('http://', '').split(':')[0];
            opt.textContent = `${alias} (${hostPort})`;
            hostSel.appendChild(opt);
          }

        hostSel.addEventListener('change', () => {
            const selectedHost = hostSel.value || null;
            initHealthCheck(selectedHost);
            loadModelsForHost(selectedHost);
          });
          
        // 初期読み込み時にデフォルトホストのモデルを取得
        await loadModelsForHost(null);
      } catch (e) {
        console.warn('ホスト一覧取得失敗', e);
      }
}

// ===== ヘルスチェック =====

async function initHealthCheck(host) {
    try {
        const url = host ? `/api/health?host=${encodeURIComponent(host)}` : '/api/health';
        const data = await apiCall(url);
        const dot = document.getElementById('health-dot');
        const text = document.getElementById('health-text');

        if (data.ollama_status === 'connected' && data.chromadb === 'connected') {
            dot.className = 'inline-block w-2.5 h-2.5 rounded-full bg-emerald-400';
            const hostName = host || 'default';
            text.textContent = `Ollama (${hostName}) + ChromaDB 接続OK`;
            text.className = 'text-emerald-400 text-sm';
          } else {
            dot.className = 'inline-block w-2.5 h-2.5 rounded-full bg-yellow-400 animate-pulse';
            text.textContent = `警告: ChromaDB=${data.chromadb} / Ollama=${data.ollama_status}`;
            text.className = 'text-yellow-400 text-sm';
          }
      } catch (e) {
        console.error('ヘルスチェック失敗', e);
      }
}

// ===== シードボタン =====

function initSeedButton() {
    document.getElementById('seed-btn').addEventListener('click', async () => {
        const btn = document.getElementById('seed-btn');
        btn.disabled = true;
        btn.textContent = '初期化中...';
        try {
            const res = await apiCall('/api/seed', 'POST');
            if (res.status === 'ok') {
                showToast(`データ初期化完了 (${res.documents_inserted}件投入)`);
              } else {
                showToast('初期化に失敗しました', 'error');
              }
          } catch (e) {
            showToast('通信エラー: ' + e.message, 'error');
          } finally {
            btn.disabled = false;
            btn.textContent = '🔄 業務サンプルデータ初期化';
          }
      });
}

// ===== タブ切り替え =====

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // アクティブ更新
            document.querySelectorAll('.tab-btn').forEach(b => {
                b.classList.remove('active', 'border-blue-500', 'text-blue-400');
                b.classList.add('border-transparent', 'text-slate-500');
            });
            btn.classList.add('active', 'border-blue-500', 'text-blue-400');
            btn.classList.remove('border-transparent', 'text-slate-500');

            // コンテンツ切り替え
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-content').forEach(sec => sec.classList.add('hidden'));
            document.getElementById(`tab-${tab}`).classList.remove('hidden');
        });
    });
}

// ===== Tab 1: 比較デモ（ストリーミング） =====

// 各エンジンのパイプライン状態を保持
const streamState = {
    naive: { steps: [], answerChunks: [], done: false },
    hybrid: { steps: [], answerChunks: [], done: false },
};

function initCompareTab() {
    document.getElementById('compare-btn').addEventListener('click', runCompareStream);
}

// SSE受信 + リアルタイムDOM更新
async function runCompareStream() {
    const query = document.getElementById('query-input').value.trim();
    if (!query) { showToast('質問文を入力してください', 'error'); return; }

    const btn = document.getElementById('compare-btn');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ 処理中...</span>';

    // 状態をリセット
    streamState.naive = { steps: [], answerChunks: [], done: false };
    streamState.hybrid = { steps: [], answerChunks: [], done: false };
    stepTimers = { naive: 0, hybrid: 0 };

    const naiveBody = document.getElementById('naive-body');
    const hybridBody = document.getElementById('hybrid-body');
    naiveBody.innerHTML = '';
    hybridBody.innerHTML = '';

    // ⏳ ブラウザが描画されるまで少し待ってからフェードイン（setTimeoutで同期実行回避）
    setTimeout(() => {
        showLoadingSpinner(naiveBody, 'naive');
        showLoadingSpinner(hybridBody, 'hybrid');
    }, 50);

    try {
        const response = await fetch('/api/compare_rag_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query,
                tenant_id: document.getElementById('tenant-select').value,
                min_year: parseInt(document.getElementById('year-select').value),
                llm_model: getSelectedModel(),
                ollama_host: getSelectedHost(),
              }),
          });

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop(); // 未完成の行は保持

            for (const chunk of lines) {
                const line = chunk.trim();
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    handleSSEEvent(data, naiveBody, hybridBody);
                } catch (parseErr) {
                    console.warn('SSEパースエラー:', parseErr, line);
                }
            }
        }

        console.log(`[SSE] Stream ended. Naive chunks: ${streamState.naive.answerChunks.length}, Hybrid chunks: ${streamState.hybrid.answerChunks.length}`);

        // ストリーム終了後に残ったバッファも処理（最終チャンク欠落対策）
        const remaining = buffer.trim();
        if (remaining.startsWith('data: ')) {
            try {
                const data = JSON.parse(remaining.slice(6));
                handleSSEEvent(data, naiveBody, hybridBody);
            } catch (parseErr) {
                console.warn('最終バッファパースエラー:', parseErr, remaining);
            }
        }
    } catch (e) {
        console.error('SSEエラー', e);
        showToast('ストリーミング検索に失敗しました: ' + e.message, 'error');
        naiveBody.innerHTML = `<p class="text-red-400 text-sm">通信エラー</p>`;
        hybridBody.innerHTML = `<p class="text-red-400 text-sm">通信エラー</p>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>🚀 比較実行</span>';
    }
}

// SSEイベントハンドラー
function handleSSEEvent(event, naiveBody, hybridBody) {
    const engine = event.engine;
    const state = streamState[engine];
    if (!state) return;

    console.log(`[SSE ${engine}] type=${event.type}`, JSON.stringify(event).slice(0, 300));

    // デバッグ: 検索ヒット件数を確認
    if (event.type === 'step_done' && (event.step_id === 'meta_filter' || event.step_id === 'vector_search')) {
        console.log(`[SSE ${engine}] 🔍 検索結果: count_before=${event.count_before}, count_after=${event.count_after}`);
    }
    if (event.type === 'chunk_preview') {
        console.log(`[SSE ${engine}] 📄 チャンク数: ${event.chunks?.length || 0}`, event.chunks);
    }

    const container = engine === 'naive' ? naiveBody : hybridBody;
    const metaId = engine === 'naive' ? 'naive-meta' : 'hybrid-meta';

    switch (event.type) {
        case 'step_start': {
            // 最初のステップでスピナー→パイプラインに切り替え（setTimeoutで描画待機）
            if (event.step_id === 'llm') {
                // LLM推論開始時は「処理中」のステップ行を追加＋スピナーを表示
                state.steps.push({ step_id: event.step_id, status: 'processing', name: event.name });
                addStepRowToContainer(container, engine, event.step_id, 'processing', event.name);
                showLoadingSpinnerForLLM(container, engine);
            } else {
                // 初期スピナー→パイプライン切り替え（即座に）
                if (!document.getElementById(`steps-${engine}`)) {
                    hideLoadingSpinner(engine);
                    renderEmptyPipeline(container, engine);
                }
                state.steps.push({ step_id: event.step_id, status: 'processing', name: event.name });
                addStepRowToContainer(container, engine, event.step_id, 'processing', event.name);
            }
            break;
        }

        case 'step_done': {
            if (event.step_id === 'llm') {
                // LLM完了時はLLMスピナーを削除
                removeLLMLoadingSpinner(engine);
            }
            const idx = state.steps.findIndex(s => s.step_id === event.step_id);
            if (idx !== -1) {
                state.steps[idx] = { ...state.steps[idx], status: event.status || 'done', duration_ms: event.duration_ms, detail: event.detail };
            } else {
                state.steps.push({ step_id: event.step_id, status: event.status || 'done', duration_ms: event.duration_ms, detail: event.detail });
            }
            updateStepRowInContainer(container, engine, event.step_id, event.status || 'done', event);
            break;
        }

        case 'chunk_preview': {
            renderChunkPreviewAccordion(container, event.chunks, engine);
            break;
        }

        case 'answer_chunk': {
            state.answerChunks.push(event.text);
            const fullAnswer = state.answerChunks.join('');
            console.log(`[SSE ${engine}] answer_chunk accumulated=${fullAnswer.length} chars`);
            updateAnswerStream(container, engine, fullAnswer, event.inference_time_ms);
            break;
        }

        case 'answer': {
            hideLoadingSpinner(engine);
            removeLLMLoadingSpinner(engine);
            state.answerChunks = [event.answer];
            finalizeAnswerDisplay(container, engine, event.answer);
            document.getElementById(metaId).textContent = `推論 ${event.inference_time_ms}ms`;
            break;
        }

        case 'done': {
            state.done = true;
            hideLoadingSpinner(engine);
            removeLLMLoadingSpinner(engine);
            // 累積した回答があれば確定表示（既存エリアも確実に上書き）
            const accumulatedAnswer = state.answerChunks.join('');
            console.log(`[SSE ${engine}] done - accumulatedAnswer length=${accumulatedAnswer.length}`);
            // 既存の空回答エリアがあれば削除してから再作成
            const existingArea = document.getElementById(`answer-area-${engine}`);
            if (existingArea) {
                const parent = existingArea.parentElement;
                if (parent) parent.removeChild(existingArea);
            }
            if (accumulatedAnswer) {
                finalizeAnswerDisplay(container, engine, accumulatedAnswer);
            } else {
                createEmptyAnswerArea(container, engine);
            }
            // 最終更新時間計算（LLMステップの時間のみ）
            const llmStep = state.steps.find(s => s.step_id === 'llm');
            const totalMs = llmStep ? (llmStep.duration_ms || 0) : state.steps.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
            document.getElementById(metaId).textContent = `推論 ${totalMs}ms`;
            updatePipelineFooter(container, engine);
            break;
        }

        case 'all_done': {
            showToast('比較検索完了');
            break;
        }

        case 'error': {
            showToast('エラー: ' + (event.message || '不明'), 'error');
            const errorP = document.createElement('p');
            errorP.className = 'text-red-400 text-sm mt-2';
            errorP.textContent = event.message;
            container.appendChild(errorP);
            break;
        }
    }
}

// 空のパイプラインコンテナを描画
function renderEmptyPipeline(container, engine) {
    const borderClass = engine === 'naive' ? 'border-red-900/20' : 'border-emerald-900/20';
    let html = `<div class="pipeline-container" id="pipeline-${engine}" style="border-color: ${borderClass};">`;
    html += '<div class="pipeline-header">';
    html += '<span class="pipeline-title">📊 検索パイプライン</span>';
    html += '</div>';
    html += `<div class="pipeline-steps" id="steps-${engine}"></div>`;
    html += `<div class="pipeline-total-time text-right mt-2 text-[10px] text-slate-600" id="footer-${engine}"></div>`;
    html += '</div>';
    container.innerHTML = html;
}

// ステップ行を追加（処理中）
function addStepRowToContainer(container, engine, stepId, status, name) {
    const stepsDiv = document.getElementById(`steps-${engine}`);
    if (!stepsDiv) return;

    const icon = STEP_ICONS[stepId] || '📌';
    const label = STEP_LABELS[stepId] || name;
    const rowId = `step-row-${engine}-${stepId}`;

    let html = `<div class="pipeline-step-row status-processing" id="${rowId}">`;
    html += `<div class="pipeline-icon">${icon}</div>`;
    html += '<div class="pipeline-info">';
    html += `<div class="pipeline-step-name">${label}</div>`;
    html += `<div class="pipeline-step-detail text-blue-400 animate-pulse">処理中...</div>`;
    html += '</div>';
    html += '</div>';

    stepsDiv.insertAdjacentHTML('beforeend', html);
}

// ステップ行を完了状態に更新
function updateStepRowInContainer(container, engine, stepId, status, event) {
    const row = document.getElementById(`step-row-${engine}-${stepId}`);
    if (!row) return;

    const icon = STEP_ICONS[stepId] || '📌';
    const label = STEP_LABELS[stepId] || '';
    const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.done;

    let detailHtml = event.detail ? `<div class="pipeline-step-detail">${escapeHtml(event.detail)}</div>` : '';
    let countHtml = '';
    if (event.count_before !== undefined && event.count_after !== undefined) {
        countHtml = `
        <div class="pipeline-count-flow">
            <span class="count-before">${event.count_before}</span>
            <span class="count-arrow">→</span>
            <span class="count-after">${event.count_after}</span>
        </div>`;
    }
    let durationHtml = event.duration_ms > 0 ? `<div class="pipeline-duration">${event.duration_ms}ms</div>` : '';

    row.className = `pipeline-step-row status-${status}`;
    row.innerHTML = `
        <div class="pipeline-icon">${cfg.icon}</div>
        <div class="pipeline-info">
            <div class="pipeline-step-name">${label}</div>
            ${detailHtml}
        </div>
        ${countHtml}
        ${durationHtml}
    `;

    // フッターを更新
    updatePipelineFooterForStep(engine, event.duration_ms || 0);
}

// パイプラインフッター（合計時間）
let stepTimers = { naive: 0, hybrid: 0 };

function updatePipelineFooterForStep(engine, ms) {
    stepTimers[engine] += ms;
    const footer = document.getElementById(`footer-${engine}`);
    if (footer) {
        footer.textContent = `合計処理時間: ${stepTimers[engine]}ms`;
    }
}

function updatePipelineFooter(container, engine) {
    updatePipelineFooterForStep(engine, 0);
}

// 回答ストリーミング更新
const answerAreaState = { naive: null, hybrid: null };

function updateAnswerStream(container, engine, text, inferenceMs) {
    let areaId = `answer-area-${engine}`;
    let area = document.getElementById(areaId);

    if (!area) {
        // 回答エリアを初めて作成
        const borderClass = engine === 'naive' ? 'border-slate-800' : 'border-emerald-900/30';
        const textClass = engine === 'naive' ? 'text-slate-300' : 'text-emerald-100';
        const div = document.createElement('div');
        div.className = 'mt-3';
        div.innerHTML = `<div id="${areaId}" class="answer-area bg-slate-950 rounded-lg p-3 text-sm ${textClass} overflow-y-auto" style="border: 1px solid ${engine === 'naive' ? 'rgba(30,41,59,1)' : 'rgba(6,78,59,0.3)'}"><div class="answer-text answer-markdown">${renderMarkdown(text)}</div><span class="answer-streaming-cursor"></span></div>`;
        container.appendChild(div);

        // メタ更新
        const metaId = engine === 'naive' ? 'naive-meta' : 'hybrid-meta';
        document.getElementById(metaId).textContent = `推論中 ${inferenceMs}ms`;
    } else {
        const textSpan = area.querySelector('.answer-text');
        if (textSpan) {
            textSpan.innerHTML = renderMarkdown(text);
        }
    }
}

// 最終回答を固定
function renderFinalAnswer(container, engine, answer, inferenceMs) {
    removeCursor(container, engine);
    const metaId = engine === 'naive' ? 'naive-meta' : 'hybrid-meta';
    document.getElementById(metaId).textContent = `推論 ${inferenceMs}ms`;
}

// カーソル削除
function removeCursor(container, engine) {
    const cursor = container.querySelector('.answer-streaming-cursor');
    if (cursor) cursor.remove();
}

// LLM推論中のスピナー表示（オーバーレイ方式）
let llmSpinnerOverlay = { naive: null, hybrid: null };

function showLoadingSpinnerForLLM(container, engine) {
    // 既存のLLMスピナーがあれば削除
    removeLLMLoadingSpinner(engine);
    
    const isNaive = engine === 'naive';
    const textColor = isNaive ? 'text-red-400' : 'text-emerald-400';
    const borderAccent = isNaive ? 'border-t-red-400' : 'border-t-emerald-400';
    
    const overlayId = `llm-overlay-${engine}`;
    const existing = document.getElementById(overlayId);
    if (existing) existing.remove();
    
    // 回答エリア位置にスピナーを配置
    const spinnerHtml = `
    <div id="${overlayId}" class="flex flex-col items-center justify-center py-8 mt-3 answer-area bg-slate-950 rounded-lg border ${borderAccent.replace('border-t-', 'border ')}">
        <div class="spinner ${borderAccent} border-slate-700 w-10 h-10 border-2"></div>
        <span class="${textColor} text-sm mt-3 font-semibold animate-pulse">LLM推論中... 回答を生成しています</span>
    </div>`;
    
    container.insertAdjacentHTML('beforeend', spinnerHtml);
    llmSpinnerOverlay[engine] = true;
}

function removeLLMLoadingSpinner(engine) {
    const overlayId = `llm-overlay-${engine}`;
    const existing = document.getElementById(overlayId);
    if (existing) {
        existing.remove();
        llmSpinnerOverlay[engine] = false;
    }
}

// 空の回答エリアを作成（ストリーミングが来なかった場合のフォールバック）
function createEmptyAnswerArea(container, engine) {
    const areaId = `answer-area-${engine}`;
    if (document.getElementById(areaId)) return; // 既に存在する場合は何もしない
    
    const div = document.createElement('div');
    div.className = 'mt-3';
    div.innerHTML = `<div id="${areaId}" class="answer-area bg-slate-950 rounded-lg p-3 text-sm ${engine === 'naive' ? 'text-slate-500' : 'text-emerald-400/60'} border ${engine === 'naive' ? 'border-slate-800' : 'border-emerald-900/30'} italic">回答が生成されませんでした</div>`;
    container.appendChild(div);
}

// 最終回答を確定表示（ストリーミングカーソル消去＋ボーダー強調）
function finalizeAnswerDisplay(container, engine, answer) {
    removeCursor(container, engine);
    const areaId = `answer-area-${engine}`;
    let area = document.getElementById(areaId);
    
    const borderClass = engine === 'naive' ? 'border-slate-800' : 'border-emerald-900/30';
    const textClass = engine === 'naive' ? 'text-slate-300' : 'text-emerald-100';

    if (area) {
        // 既存の回答エリアを確定スタイルに切り替え
        const textSpan = area.querySelector('.answer-text');
        if (textSpan) {
            textSpan.innerHTML = renderMarkdown(answer);
        } else {
            area.innerHTML = `<div class="answer-text answer-markdown">${renderMarkdown(answer)}</div>`;
        }
        area.className = `answer-area bg-slate-950 rounded-lg p-3 text-sm ${textClass} border ${borderClass} overflow-y-auto`;
    } else {
        // 回答エリアが存在しない場合は新規作成（ストリーミングが来なかった場合）
        const div = document.createElement('div');
        div.className = 'mt-3';
        div.innerHTML = `<div id="${areaId}" class="answer-area bg-slate-950 rounded-lg p-3 text-sm ${textClass} border ${borderClass} overflow-y-auto"><div class="answer-text answer-markdown">${renderMarkdown(answer)}</div></div>`;
        container.appendChild(div);
    }
}

// チャンクプレビューアコーディオン
function renderChunkPreviewAccordion(container, chunks, engine) {
    let listEl = container.querySelector('.chunk-preview-list');
    const isNaive = engine === 'naive';
    const borderColor = isNaive ? 'border-red-800/30' : 'border-emerald-800/30';
    const badgeColor = isNaive ? 'bg-red-900 text-red-300' : 'bg-emerald-900 text-emerald-300';

    let html = '<div class="mt-3 space-y-2">';
    html += `<p class="text-xs font-semibold text-slate-500">${isNaive ? '参照チャンク:' : '参照チャンク（RRF統合順）:'}</p>`;

    chunks.forEach((c, i) => {
        const accordionId = `chunk-content-${engine}-${i}`;
        const arrowId = `arrow-chunk-${engine}-${i}`;

        html += `<div class="chunk-preview-card bg-slate-950 border ${borderColor} rounded-lg overflow-hidden">`;
        // ヘッダー（クリックで展開）
        html += `<div class="px-3 py-2 flex items-center gap-2 cursor-pointer hover:bg-slate-800/50 transition" onclick="toggleChunkPreview('${accordionId}', '${arrowId}')">`;
        html += `<span class="chunk-badge ${badgeColor}">${escapeHtml(c.title || '無題')}</span>`;

        // スコアバッジ
        if (c.distance !== undefined) {
            html += `<span class="text-[10px] text-slate-500">dist: ${c.distance}</span>`;
        }
        if (c.rrf_score !== undefined) {
            html += `<span class="chunk-badge bg-emerald-900 text-emerald-300">vec#${c.vector_rank || '?'}</span>`;
            html += `<span class="chunk-badge bg-purple-900 text-purple-300">bm25#${c.bm25_rank || '?'}</span>`;
            html += `<span class="chunk-badge bg-amber-900 text-amber-300">rrf=${c.rrf_score}</span>`;
        }

        // アロー
        html += `<svg id="${arrowId}" class="chunk-arrow w-3 h-3 ml-auto text-slate-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>`;
        html += '</div>';

        // 展開コンテンツ
        html += `<div id="${accordionId}" class="hidden border-t border-slate-800/50">`;
        html += `<div class="px-3 py-2 text-xs text-slate-400 leading-relaxed whitespace-pre-wrap">${escapeHtml(c.text_preview || '')}</div>`;
        html += '</div>';
        html += '</div>';
    });

    html += '</div>';

    if (listEl) {
        listEl.outerHTML = `<div class="chunk-preview-list">${html}</div>`;
    } else {
        container.insertAdjacentHTML('beforeend', `<div class="chunk-preview-list">${html}</div>`);
    }
}

window.toggleChunkPreview = function(contentId, arrowId) {
    const content = document.getElementById(contentId);
    const arrow = document.getElementById(arrowId);
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        arrow.style.transform = 'rotate(180deg)';
    } else {
        content.classList.add('hidden');
        arrow.style.transform = '';
    }
};

// ===== パイプラインレンダリング =====

const STEP_ICONS = {
    embed: '🔤',
    meta_filter: '🏷️',
    vector_search: '📐',
    bm25: '🔑',
    rerank: '⚖️',
    llm: '🧠',
};

const STEP_LABELS = {
    embed: 'ベクトル化',
    meta_filter: 'メタフィルタ',
    vector_search: 'ベクトル検索',
    bm25: 'BM25スコアリング',
    rerank: 'RRFリランキング',
    llm: 'LLM推論',
};

const STATUS_CONFIG = {
    done: { icon: '✓', label: '完了' },
    skipped: { icon: '⊘', label: '未適用' },
    warning: { icon: '⚠', label: '警告' },
};

function renderPipelineSteps(steps, container) {
    if (!steps || steps.length === 0) return '';

    let totalMs = 0;
    let html = '<div class="pipeline-container">';
    html += '<div class="pipeline-header">';
    html += '<span class="pipeline-title">📊 検索パイプライン</span>';
    html += '</div>';

    steps.forEach(step => {
        const statusClass = `status-${step.status}`;
        const icon = STEP_ICONS[step.step_id] || '📌';
        const label = STEP_LABELS[step.step_id] || step.name;
        const cfg = STATUS_CONFIG[step.status] || STATUS_CONFIG.done;
        totalMs += step.duration_ms || 0;

        html += `<div class="pipeline-step-row ${statusClass}">`;
        // アイコン
        html += `<div class="pipeline-icon">${cfg.icon}</div>`;
        // 情報
        html += '<div class="pipeline-info">';
        html += `<div class="pipeline-step-name">${label}</div>`;
        if (step.detail) {
            html += `<div class="pipeline-step-detail">${escapeHtml(step.detail)}</div>`;
        }
        html += '</div>';
        // カウントフロー
        if (step.count_before !== undefined && step.count_after !== undefined) {
            const afterColor = step.status === 'skipped' ? 'count-after' : 'count-after';
            html += `<div class="pipeline-count-flow">`;
            html += `<span class="count-before">${step.count_before}</span>`;
            html += `<span class="count-arrow">→</span>`;
            html += `<span class="${afterColor}">${step.count_after}</span>`;
            html += `</div>`;
        }
        // タイマー
        if (step.duration_ms > 0) {
            html += `<div class="pipeline-duration">${step.duration_ms}ms</div>`;
        }
        html += '</div>';
    });

    // 合計時間フッター
    html += `<div class="pipeline-total-time text-right mt-2 text-[10px] text-slate-600">`;
    html += `合計処理時間: ${totalMs}ms`;
    html += '</div>';
    html += '</div>';

    return html;
}

function renderNaiveResult(result, container) {
    if (!result || !result.answer) {
        container.innerHTML = '<p class="text-slate-500 text-sm">回答が取得できませんでした</p>';
        return;
    }
    document.getElementById('naive-meta').textContent =
        `推論 ${result.inference_time_ms}ms`;

    let html = '';

    // パイプライン表示
    if (result.pipeline_steps && result.pipeline_steps.length) {
        html += renderPipelineSteps(result.pipeline_steps);
        html += '<div class="mt-3"></div>';
    }

    // 回答エリア
    html += `<div class="answer-area bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-300 overflow-y-auto"><div class="answer-text answer-markdown">${renderMarkdown(result.answer)}</div></div>`;

    // チャンク一覧
    if (result.chunks && result.chunks.length) {
        html += '<div class="mt-3 space-y-2">';
        html += '<p class="text-xs font-semibold text-slate-500">参照チャンク:</p>';
        result.chunks.forEach(c => {
            const isNoise = c.tenant_id !== 'A社_都市型' || (c.effective_year && c.effective_year < 2025);
            html += `<div class="bg-slate-950 border ${isNoise ? 'border-red-800/50' : 'border-slate-700'} rounded p-2 text-xs">`;
            html += `<span class="chunk-badge ${isNoise ? 'bg-red-900 text-red-300' : 'bg-slate-700 text-slate-300'}">${escapeHtml(c.tenant_id)}</span>`;
            html += `<span class="chunk-badge bg-slate-800 text-slate-400">${c.effective_year}年</span>`;
            html += `<span class="text-slate-400 block truncate">${escapeHtml(c.title)}</span>`;
            if (isNoise) {
                html += `<div class="text-red-400 text-[10px] mt-1">⚠️ テナント/年度が不一致（ノイズ）</div>`;
            }
            html += '</div>';
        });
        html += '</div>';
    }
    container.innerHTML = html;
}

function renderHybridResult(result, container) {
    if (!result || !result.answer) {
        container.innerHTML = '<p class="text-slate-500 text-sm">回答が取得できませんでした</p>';
        return;
    }
    document.getElementById('hybrid-meta').textContent =
        `推論 ${result.inference_time_ms}ms / プロンプト ${result.prompt_length}文字`;

    let html = '';

    // パイプライン表示
    if (result.pipeline_steps && result.pipeline_steps.length) {
        html += renderPipelineSteps(result.pipeline_steps);
        html += '<div class="mt-3"></div>';
    }

    // 回答エリア
    html += `<div class="answer-area bg-slate-950 border border-emerald-900/30 rounded-lg p-3 text-sm text-emerald-100 overflow-y-auto"><div class="answer-text answer-markdown">${renderMarkdown(result.answer)}</div></div>`;

    // チャンク一覧（RRFスコア付き）
    if (result.chunks && result.chunks.length) {
        html += '<div class="mt-3 space-y-2">';
        html += '<p class="text-xs font-semibold text-slate-500">参照チャンク（RRF統合順）:</p>';
        result.chunks.forEach(c => {
            html += `<div class="bg-slate-950 border border-emerald-800/30 rounded p-2 text-xs">`;
            html += `<span class="chunk-badge bg-emerald-900 text-emerald-300">vec#${c.vector_rank}</span>`;
            html += `<span class="chunk-badge bg-purple-900 text-purple-300">bm25#${c.bm25_rank}</span>`;
            html += `<span class="chunk-badge bg-amber-900 text-amber-300">rrf=${c.rrf_score}</span>`;
            html += `<span class="text-slate-400 block truncate mt-1">${escapeHtml(c.title)}</span>`;
            html += '</div>';
        });
        html += '</div>';
    }
    container.innerHTML = html;
}

// ===== Tab 2: 日次要約バッチ =====

const EMAILS_DATA = [
    { source: '渋谷支店', body: '本入居予定のフリーランスエンジニアの方について：業務委託契約書は月額55万円だが、契約期間が2ヶ月で更新見込みとのこと。A社特例ルールでは3ヶ月以上の基本契約書が必要ですが、2ヶ月+更新意向書のセットでも承認可能でしょうか？' },
    { source: '新宿支店', body: '入居者からの苦情：専用部エアコンが動かなくなっており、修繕申請を5日前にポータル登録済みですが未対応です。お客様は「契約書では7営業日以内」と主張していますが、実際には9営業日経過しています。' },
    { source: '横浜支店', body: '退去手続き中のお客様から「壁紙の薄い黄ばみは経年劣化だから負担したくない」との主張あり。A社管理規約では「薄い日焼け程度」が入居者不要範囲と明記されていますが...' },
    { source: '埼玉支店', body: 'B社物件に入居希望の個人事業主から、確定申告書は直近1年分しか作成していないとの連絡あり。B社ルールでは3年分必須と理解していますが、例外的な緩和条件はありますか？' },
    { source: '千葉支店', body: 'C社法人契約のお客様（従業員15名）の定期点検日程調整について：次回点検は入居から6ヶ月目にあたりますが、来月は法人事業部の連休に重なり担当者が不在です...' },
];

function initSummaryTab() {
    // メールプレビュー表示
    const preview = document.getElementById('emails-preview');
    EMAILS_DATA.forEach((e, i) => {
        const div = document.createElement('div');
        div.className = 'bg-slate-950 border border-slate-800 rounded p-2';
        div.innerHTML = `<span class="text-amber-500 font-semibold">[${e.source}]</span> <span class="text-slate-400">${escapeHtml(e.body)}</span>`;
        preview.appendChild(div);
    });

    document.getElementById('summary-btn').addEventListener('click', runSummary);
}

async function runSummary() {
    const btn = document.getElementById('summary-btn');
    btn.disabled = true;
    btn.textContent = '要約処理中...';

    try {
        const data = await apiCall('/api/daily_summary', 'POST', { llm_model: getSelectedModel(), ollama_host: getSelectedHost() });

        if (data.status === 'error') throw new Error(data.message);

        document.getElementById('summary-result').classList.remove('hidden');
        renderSummaryTable(data.answer, data.inference_time_ms);
        showToast(`要約完了 (${data.inference_time_ms}ms)`);
    } catch (e) {
        showToast('要約処理に失敗しました: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⚡ ローカルLLMで一括構造化・要約';
    }
}

function renderSummaryTable(rawAnswer, timeMs) {
    const container = document.getElementById('summary-table');

    // LLMの回答が JSON 形式の場合にパースを試みる
    let parsed = null;
    try {
        // マークダウン内の JSON ブロックを抽出
        const jsonMatch = rawAnswer.match(/\[[\s\S]*\]/);
        if (jsonMatch) parsed = JSON.parse(jsonMatch[0]);
    } catch { /* 構造化でない場合は raw表示にフォールバック */ }

    if (parsed && Array.isArray(parsed)) {
        let html = `<p class="text-xs text-slate-500 mb-2">推論時間: ${timeMs}ms</p>`;
        html += '<table class="w-full text-sm border-collapse">';
        html += '<thead><tr class="border-b border-slate-700">';
        html += '<th class="text-left py-2 px-2 text-slate-400">メール</th>';
        html += '<th class="text-center py-2 px-2 text-slate-400">緊急度</th>';
        html += '<th class="text-left py-2 px-2 text-slate-400">テナント</th>';
        html += '<th class="text-left py-2 px-2 text-slate-400">要約</th>';
        html += '<th class="text-left py-2 px-2 text-slate-400">推奨アクション</th>';
        html += '</tr></thead><tbody>';

        parsed.forEach(row => {
            const urgencyColor = row.urgency === '高' ? 'text-red-400' : row.urgency === '中' ? 'text-yellow-400' : 'text-emerald-400';
            html += '<tr class="border-b border-slate-800/50">';
            html += `<td class="py-2 px-2 text-slate-300">${escapeHtml(row.mail_id || '')}</td>`;
            html += `<td class="text-center py-2 px-2 font-bold ${urgencyColor}">${escapeHtml(row.urgency || '')}</td>`;
            html += `<td class="py-2 px-2 text-slate-300">${escapeHtml(row.tenant || '')}</td>`;
            html += `<td class="py-2 px-2 text-slate-400 max-w-xs truncate" title="${escapeHtml(row.summary || '')}">${escapeHtml(row.summary || '')}</td>`;
            html += `<td class="py-2 px-2 text-slate-400 max-w-xs truncate" title="${escapeHtml(row.recommended_action || '')}">${escapeHtml(row.recommended_action || '')}</td>`;
            html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    } else {
        // JSONパース失敗時はrawテキスト表示
        container.innerHTML = `<pre class="answer-area bg-slate-950 border border-slate-800 rounded p-3 text-xs text-slate-400">${escapeHtml(rawAnswer)}</pre>`;
    }
}

// ===== エスケープ処理 =====

function escapeHtml(str) {
    if (!str) return '';
    const map = { '&': '&', '<': '<', '>': '>', '"': '"', "'": '&#039;' };
    return String(str).replace(/[&<>"']/g, c => map[c]);
}


// ===== Tab 3: データブラウザ =====

const TENANT_COLORS = {
    'A社_都市型': { bg: 'bg-blue-900', text: 'text-blue-300', border: 'border-blue-800/40' },
    'B社_全国賃貸': { bg: 'bg-orange-900', text: 'text-orange-300', border: 'border-orange-800/40' },
    'C社_社宅代行': { bg: 'bg-teal-900', text: 'text-teal-300', border: 'border-teal-800/40' },
};

function initBrowserTab() {
    populateFilterDropdowns();
    document.getElementById('browser-search-btn').addEventListener('click', fetchDocuments);
    document.getElementById('browser-clear-btn').addEventListener('click', clearFilters);

    // Enterキーでも検索
    document.getElementById('browser-search').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') fetchDocuments();
    });

    // フィルタ変更時にも即時検索
    ['browser-tenant', 'browser-doctype', 'browser-year'].forEach(id => {
        document.getElementById(id).addEventListener('change', fetchDocuments);
    });
}

async function populateFilterDropdowns() {
    try {
        const data = await apiCall('/api/collection/values');
        populateSelect('browser-tenant', data.tenant_ids || []);
        populateSelect('browser-doctype', data.doc_types || []);
        populateSelect('browser-year', (data.effective_years || []).map(y => String(y)));
    } catch (e) {
        console.warn('フィルタ値の取得に失敗', e);
    }
}

function populateSelect(id, values) {
    const sel = document.getElementById(id);
    sel.innerHTML = '<option value="">全部</option>';
    values.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = v;
        sel.appendChild(opt);
    });
}

async function fetchDocuments() {
    const params = new URLSearchParams();
    const tenant = document.getElementById('browser-tenant').value;
    const docType = document.getElementById('browser-doctype').value;
    const year = document.getElementById('browser-year').value;
    const search = document.getElementById('browser-search').value.trim();

    if (tenant) params.set('tenant_id', tenant);
    if (docType) params.set('doc_type', docType);
    if (year) params.set('effective_year', year);
    if (search) params.set('search_text', search);
    params.set('limit', '100');

    const container = document.getElementById('browser-docs-list');
    container.innerHTML = '<div class="flex items-center justify-center py-12"><div class="spinner"></div><span class="ml-2 text-sm text-slate-500">読み込み中...</span></div>';

    try {
        const url = `/api/collection/browser?${params.toString()}`;
        const data = await apiCall(url);

        if (data.error) {
            container.innerHTML = `<p class="text-amber-400 text-sm">${escapeHtml(data.error)}</p>`;
            updateCountBar(0, 0);
            return;
        }

        renderDocuments(data.documents || [], container);
        updateCountBar(data.filtered_count || 0, data.total_count || 0);
        updateClearButtonVisibility();
    } catch (e) {
        container.innerHTML = `<p class="text-red-400 text-sm">エラー: ${e.message}</p>`;
    }
}

function renderDocuments(docs, container) {
    if (!docs || docs.length === 0) {
        container.innerHTML = '<p class="text-slate-500 text-sm text-center py-8">該当するドキュメントが見つかりませんでした</p>';
        return;
    }

    let html = '';
    docs.forEach((doc, idx) => {
        const colors = TENANT_COLORS[doc.tenant_id] || { bg: 'bg-slate-700', text: 'text-slate-300', border: 'border-slate-700' };

        html += `
        <div class="bg-slate-900 border ${colors.border} rounded-xl overflow-hidden transition hover:border-slate-600">
            <div class="px-4 py-3 flex items-center justify-between cursor-pointer hover:bg-slate-800/50 transition" onclick="toggleAccordion('doc-${idx}')">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span class="text-xs text-slate-600 font-mono">${escapeHtml(doc.id)}</span>
                    <span class="chunk-badge ${colors.bg} ${colors.text}">${escapeHtml(doc.tenant_id)}</span>
                    <span class="chunk-badge bg-purple-900 text-purple-300">${escapeHtml(doc.doc_type || '')}</span>
                    <span class="chunk-badge bg-slate-800 text-slate-400">${doc.effective_year || '?'}年</span>
                </div>
                <div class="flex items-center gap-2 ml-3">
                    <span class="text-sm font-semibold text-slate-300 truncate max-w-xs">${escapeHtml(doc.title)}</span>
                    <svg id="arrow-doc-${idx}" class="w-4 h-4 text-slate-500 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </div>
            </div>
            <div id="accordion-doc-${idx}" class="hidden border-t border-slate-800/50">
                <div class="px-4 py-3 space-y-2">
                    <div class="grid grid-cols-3 gap-2 text-xs text-slate-500">
                        <div><span class="font-semibold">ID:</span> ${escapeHtml(doc.id)}</div>
                        <div><span class="font-semibold">テナント:</span> ${escapeHtml(doc.tenant_id)}</div>
                        <div><span class="font-semibold">年度:</span> ${doc.effective_year || '-'}</div>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 rounded p-3 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">${escapeHtml(doc.text)}</div>
                </div>
            </div>
        </div>`;
    });

    container.innerHTML = html;
}

window.toggleAccordion = function(id) {
    const content = document.getElementById(`accordion-${id}`);
    const arrow = document.getElementById(`arrow-${id}`);
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        arrow.style.transform = 'rotate(180deg)';
    } else {
        content.classList.add('hidden');
        arrow.style.transform = '';
    }
};

function updateCountBar(filtered, total) {
    const bar = document.getElementById('browser-count-bar');
    const stats = document.getElementById('collection-stats');

    bar.querySelector('span').textContent = `全 ${total} 件中 ${filtered} 件該当`;
    if (stats) {
        stats.innerHTML = `<span>コレクション: b2b_tenant_rules</span><span>全 ${total} ドキュメント</span>`;
    }
}

function clearFilters() {
    document.getElementById('browser-tenant').value = '';
    document.getElementById('browser-doctype').value = '';
    document.getElementById('browser-year').value = '';
    document.getElementById('browser-search').value = '';
    updateClearButtonVisibility();
    fetchDocuments();
}

function updateClearButtonVisibility() {
    const btn = document.getElementById('browser-clear-btn');
    const hasFilter = (
        document.getElementById('browser-tenant').value ||
        document.getElementById('browser-doctype').value ||
        document.getElementById('browser-year').value ||
        document.getElementById('browser-search').value.trim()
    );
    btn.classList.toggle('hidden', !hasFilter);
}

// ===== ストリーミング状態管理 =====

// ===== ローディングスピナー関連 =====

function showLoadingSpinner(container, engine) {
    const isNaive = engine === 'naive';
    const textColor = isNaive ? 'text-red-400' : 'text-emerald-400';
    const borderAccent = isNaive ? 'border-t-red-400' : 'border-t-emerald-400';
    
    const spinnerHtml = `
    <div id="spinner-${engine}" class="flex flex-col items-center justify-center py-16">
        <div class="spinner ${borderAccent} border-slate-700 w-10 h-10 border-2"></div>
        <span class="${textColor} text-sm mt-3 font-semibold animate-pulse">検索中...</span>
    </div>`;
    container.innerHTML = spinnerHtml;
}

function hideLoadingSpinner(engine) {
    const spinner = document.getElementById(`spinner-${engine}`);
    if (spinner) spinner.remove();
}
