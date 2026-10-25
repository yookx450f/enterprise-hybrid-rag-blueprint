/**
 * Phase 4: ダッシュボード用JavaScript
 * API通信・タブ切り替え・結果表示のロジック
 */

// ===== ユーティリティ =====

function getSelectedModel() {
    const sel = document.getElementById('model-select');
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

// ===== 初期化処理 =====

document.addEventListener('DOMContentLoaded', () => {
    initHealthCheck();
    initTabs();
    initCompareTab();
    initSummaryTab();
    initBrowserTab();
});

// ===== ヘルスチェック =====

async function initHealthCheck() {
    try {
        const data = await apiCall('/api/health');
        const dot = document.getElementById('health-dot');
        const text = document.getElementById('health-text');
        const modelSel = document.getElementById('model-select');

        if (data.ollama_status === 'connected' && data.chromadb === 'connected') {
            dot.className = 'inline-block w-2.5 h-2.5 rounded-full bg-emerald-400';
            text.textContent = 'Ollama + ChromaDB 接続OK';
            text.className = 'text-emerald-400 text-sm';
        } else {
            dot.className = 'inline-block w-2.5 h-2.5 rounded-full bg-yellow-400 animate-pulse';
            text.textContent = `警告: ChromaDB=${data.chromadb} / Ollama=${data.ollama_status}`;
            text.className = 'text-yellow-400 text-sm';
        }

        if (data.available_models && data.available_models.length) {
            modelSel.classList.remove('hidden');
            data.available_models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSel.appendChild(opt);
            });
            // デフォルトで completion 対応モデルを選ぶ
            const pref = data.available_models.find(m => !m.includes('bge') && !m.includes('embed'));
            if (pref) modelSel.value = pref;
        }
    } catch (e) {
        console.error('ヘルスチェック失敗', e);
    }

    // シードボタン
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

// ===== Tab 1: 比較デモ =====

function initCompareTab() {
    document.getElementById('compare-btn').addEventListener('click', runCompare);
}

async function runCompare() {
    const query = document.getElementById('query-input').value.trim();
    if (!query) { showToast('質問文を入力してください', 'error'); return; }

    const bodyEl = document.getElementById('naive-body');
    const hBodyEl = document.getElementById('hybrid-body');
    bodyEl.innerHTML = '<div class="flex items-center justify-center py-12"><div class="spinner"></div><span class="ml-2 text-sm text-slate-500">検索中...</span></div>';
    hBodyEl.innerHTML = bodyEl.innerHTML;

    try {
        const data = await apiCall('/api/compare_rag', 'POST', {
            query,
            tenant_id: document.getElementById('tenant-select').value,
            min_year: parseInt(document.getElementById('year-select').value),
            llm_model: getSelectedModel(),
        });

        if (data.status === 'error') throw new Error(data.message);
        renderNaiveResult(data.naive_rag, bodyEl);
        renderHybridResult(data.hybrid_rag, hBodyEl);
    } catch (e) {
        bodyEl.innerHTML = `<p class="text-red-400 text-sm">エラー: ${e.message}</p>`;
        hBodyEl.innerHTML = bodyEl.innerHTML;
        showToast('比較検索に失敗しました', 'error');
    }
}

function renderNaiveResult(result, container) {
    if (!result || !result.answer) {
        container.innerHTML = '<p class="text-slate-500 text-sm">回答が取得できませんでした</p>';
        return;
    }
    document.getElementById('naive-meta').textContent =
        `推論 ${result.inference_time_ms}ms`;

    let html = '';
    // 回答エリア
    html += `<div class="answer-area bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-300">${escapeHtml(result.answer)}</div>`;

    // チャンク一覧
    if (result.chunks && result.chunks.length) {
        html += '<div class="mt-3 space-y-2">';
        html += '<p class="text-xs font-semibold text-slate-500">参照チャンク:</p>';
        result.chunks.forEach(c => {
            const isNoise = c.tenant_id !== 'A社_都市型' || (c.effective_year && c.effective_year < 2025);
            html += `<div class="bg-slate-950 border ${isNoise ? 'border-red-800/50' : 'border-slate-700'} rounded p-2 text-xs">`;
            html += `<span class="chunk-badge ${isNoise ? 'bg-red-900 text-red-300' : 'bg-slate-700 text-slate-300'}">${c.tenant_id}</span>`;
            html += `<span class="chunk-badge bg-slate-800 text-slate-400">${c.effective_year}年</span>`;
            html += `<span class="text-slate-400 block truncate">${escapeHtml(c.title)}</span>`;
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
    // 回答エリア
    html += `<div class="answer-area bg-slate-950 border border-emerald-900/30 rounded-lg p-3 text-sm text-emerald-100">${escapeHtml(result.answer)}</div>`;

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
        const data = await apiCall('/api/daily_summary', 'POST', { llm_model: getSelectedModel() });

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
