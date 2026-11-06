const state = { dashboard: null, settings: null, view: 'watch', runTimer: null, connected: null, startingRun: false, polling: false, refreshing: false, listingActions: new Set() };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
      signal: options.signal || AbortSignal.timeout(15000),
    });
  } catch (error) {
    setConnectionState(false);
    throw new Error('网站后台未连接，请双击桌面的“赤色彗星补给战情室”启动，再点击“重新连接”。');
  }
  setConnectionState(true);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `请求失败 (${response.status})`);
  return data;
}

function setConnectionState(connected) {
  state.connected = connected;
  $('#connectionBanner').hidden = connected;
  $('#loginButton').disabled = !connected;
  $('#pddLoginButton').disabled = !connected;
  renderStatus(state.dashboard?.latest_run, Boolean(state.dashboard?.tracking));
}

function toast(message, error = false) {
  const box = $('#toast');
  box.textContent = message;
  box.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(box._timer);
  box._timer = setTimeout(() => { box.className = 'toast'; }, 3600);
}

function yuan(value) {
  return value === null || value === undefined ? '—' : `¥${Number(value).toFixed(2)}`;
}

function platformLabel(entry) { return entry?.platform === 'pdd' ? '拼多多' : '闲鱼'; }
function readableError(value) {
  return String(value || '').replace(/\[[^\]]+\]\s*/g, '')
    .replace(/BrowserContext\.new_page: Target page, context or browser has been closed/g,
      '采集浏览器已关闭，请重新搜索并保留自动打开的窗口直到完成');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function blockListingButton(entry) {
  return entry?.id ? `<button type="button" class="text-button block-listing-button" data-listing-id="${Number(entry.id)}" aria-label="删除并屏蔽：${escapeHtml(entry.title)}" title="移除这条商品，以后搜索也不再显示">删除并屏蔽</button>` : '';
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

// 只保留"年-月-日 时:分"，卡片上空间有限，秒没有意义。
function shortDate(value) {
  if (!value) return '日期未知';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16);
  const pad = number => String(number).padStart(2, '0');
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

// 这张卡片**自己那条报价**的采集时间。
//
// ⚠ 不要拿"最近一次 run 的时间"兜底：最近一次 run 可能是失败的，
// 卡片应始终使用该条报价自己的采集时间。
// 卡片根本没有数据时返回 null，由调用方显示"尚无采集数据"。
function collectedAtOf(summary) {
  const listing = summary.current || summary.unknown_shipping;
  return listing?.observed_at || summary.top_three?.[0]?.observed_at || null;
}

function renderCard(item) {
  const summary = item.summary || {};
  const current = summary.current;
  const unknown = summary.unknown_shipping;
  const history = summary.history;
  const listing = current || unknown;
  const topThree = summary.top_three || [];
  const pendingShipping = summary.shipping_pending || [];
  const pendingReview = summary.review_pending || [];
  const price = current ? yuan(current.total_price) : unknown ? `${yuan(unknown.item_price)}+运费` : '—';
  const shipping = current
    ? (current.shipping_status === 'platform' ? `运费 ${yuan(current.shipping_price)}` : current.shipping_status === 'conditional' ? '条件包邮，未计入' : current.shipping_status === 'free_badge' ? '平台标注包邮' : '已含运费')
    : unknown ? '运费待确认（未计入排名）' : '尚无合格商品';
  const badgeClass = unknown || (current && current.shipping_status === 'conditional') ? 'badge warn' : 'badge';
  const serial = String(item.id).padStart(2, '0');
  const comparePrice = current?.total_price ?? null;
  const delta = comparePrice !== null && item.msrp_cny ? comparePrice - item.msrp_cny : null;
  const deltaHtml = delta === null
    ? '<span class="price-delta">MSRP DELTA // —</span>'
    : `<span class="price-delta ${delta <= 0 ? 'good' : 'high'}">MSRP DELTA // ${delta > 0 ? '+' : ''}${yuan(delta)}</span>`;

  // ★ 新鲜度：这一批数据是什么时候采的、是不是旧批次。
  // 每张卡片显示各自的报价采集日期。
  const collectedAt = collectedAtOf(summary);
  const isStale = Boolean(summary.is_stale);
  const staleTag = isStale
    ? '<span class="stale-tag">旧批次 · 非本次采集</span>'
    : '';
  const currentLabel = isStale ? 'STALE // 旧批次' : 'LIVE FLOOR // 当前';
  const currentNote = collectedAt
    ? `采集于 ${shortDate(collectedAt)}`
    : (listing ? '采集时间未知' : '尚无采集数据');

  // ★ 规则可信度：规则改版前（rule_version 更小）判出来的记录要标出来。
  // 旧记录**不删**，只标注，避免"历史最低价"被旧规则的误判长期占着还不自知。
  // 注意 Boolean(entry)：没有数据（猎犬/加萨C 这种本次无合格结果）时
  // entry 是 null，不能因为 Number(undefined ?? 0) = 0 < 规则版本 就误标"旧规则"。
  const ruleVersion = Number(state.dashboard?.filter_rule_version || 0);
  const isOldRule = entry => Boolean(entry) && Number(entry.rule_version ?? 0) < ruleVersion;
  const oldRuleTag = entry => (isOldRule(entry) ? '<span class="old-rule-tag">旧规则数据</span>' : '');

  const cardActions = item.active
    ? `<button class="text-button purchased-button" data-id="${item.id}">✓ 标记已购入</button>`
    : `<button class="text-button restore-button" data-id="${item.id}">↩ 恢复关注</button>`;
  const purchaseStamp = item.active ? '' : `
    <div class="purchase-stamp">
      <span>SECURED // ${item.purchased_at ? escapeHtml(item.purchased_at.slice(0, 10)) : 'DATE UNKNOWN'}</span>
      <strong>${item.purchase_price_cny === null || item.purchase_price_cny === undefined ? '成交价未记录' : `${yuan(item.purchase_price_cny)} 入库`}</strong>
    </div>`;
  const listingHtml = topThree.length ? `
    <div class="listing-heading"><span>LOWEST THREE // 两平台最低三件</span><small>${escapeHtml(currentNote)} · 含运费价格由低到高</small></div>
    <div class="listing-stack">${topThree.map((entry, index) => {
      const entryPrice = entry.total_price === null
        ? `${yuan(entry.item_price)}+运费`
        : yuan(entry.total_price);
      const entryShipping = entry.total_price === null
        ? (entry.shipping_status === 'conditional' ? '条件包邮，待确认' : '运费待确认')
        : entry.shipping_status === 'platform' ? `运费 ${yuan(entry.shipping_price)}` : entry.shipping_status === 'free_badge' ? '平台标注包邮' : '已含运费';
      return `<div class="listing-row">
        <b>0${index + 1}</b>
        <div><a href="${escapeHtml(entry.detail_url)}" target="_blank" rel="noreferrer">${escapeHtml(entry.title)}</a>
        <small><b>${platformLabel(entry)}</b>${entry.is_stale ? ' · 旧报价，待更新' : ''} · ${escapeHtml(entry.origin || '发货地未显示')} · ${entryShipping} ${entry.price_note ? `· ${escapeHtml(entry.price_note)}` : ''} · 采集 ${escapeHtml(shortDate(entry.observed_at))}</small>${blockListingButton(entry)}</div>
        <strong>${entryPrice}</strong>
      </div>`;
    }).join('')}</div>`
    : listing ? `
    <a href="${escapeHtml(listing.detail_url)}" target="_blank" rel="noreferrer">${escapeHtml(listing.title)}</a>
    ${blockListingButton(listing)}
    <div class="listing-meta"><span class="${badgeClass}">${shipping}</span><span>${escapeHtml(listing.origin || '发货地未显示')}</span><span>采集 ${escapeHtml(shortDate(listing.observed_at))}</span></div>`
    : `<span style="color:var(--muted);font-size:11px">完成首次价格检查后显示结果</span>`;

  return `<article class="model-card${item.active ? '' : ' secured'}${isStale ? ' stale' : ''}" data-serial="${serial}">
    ${purchaseStamp}
    <div class="card-head">
      <div><span class="card-kicker">TARGET // ${serial}</span><h2>${escapeHtml(item.name)}</h2><span class="model-code">${escapeHtml(item.model_code)}</span></div>
      <div class="card-menu"><button class="icon-button edit-button" data-id="${item.id}" title="编辑">✎</button></div>
    </div>
    <div class="price-strip">
      <div class="price-box current${isStale ? ' stale' : ''}"><span>${currentLabel}</span><strong>${price}</strong><small>${shipping}</small><small class="collected">${escapeHtml(currentNote)}</small>${staleTag}${oldRuleTag(listing)}</div>
      <div class="price-box"><span>HIST. LOW // 历史</span><strong>${history ? yuan(history.total_price) : '—'}</strong><small>${history ? `${shortDate(history.observed_at)} 采集` : '从首次运行累计'}</small>${oldRuleTag(history)}${history ? `<div class="quote-controls"><a href="${escapeHtml(history.detail_url)}" target="_blank" rel="noreferrer" title="${escapeHtml(history.title)}">查看商品</a>${blockListingButton(history)}</div>` : ''}</div>
      <div class="price-box msrp-price"><span>日元含税发售价</span><strong>${item.msrp_jpy > 0 ? `¥${Number(item.msrp_jpy).toLocaleString()}` : '—'}</strong><small>${item.msrp_jpy > 0 ? (item.msrp_cny != null ? `≈ ${yuan(item.msrp_cny)}<br>人民币参考价` : '等待获取汇率') : '日元发售价待补全，请点编辑'}</small>${item.official_url ? `<a href="${escapeHtml(item.official_url)}" target="_blank" rel="noreferrer">官方参考</a>` : ''}</div>
    </div>
    <div class="marketplace-lowest">${['xianyu','pdd'].map(platform => {
      const entry = (summary.marketplace_lowest || []).find(row => row.platform === platform);
      return entry ? `<span class="quote-controls"><a href="${escapeHtml(entry.detail_url)}" target="_blank" rel="noreferrer">${platformLabel(entry)}最低：<b>${yuan(entry.total_price)}</b>${entry.is_stale ? ' · 旧报价' : ''}${entry.price_note ? ` · ${escapeHtml(entry.price_note)}` : ''}</a>${blockListingButton(entry)}</span>`
        : `<span>${platformLabel({platform})}：${pendingShipping.some(row=>row.platform===platform) ? '运费待确认' : pendingReview.some(row=>row.platform===platform) ? '有待核实报价' : '尚无确认报价'}</span>`;
    }).join('')}</div>
    <div class="listing">${listingHtml}</div>
    ${pendingShipping.length ? `<div class="shipping-pending">
      <b>运费待确认 · 另有 ${pendingShipping.length} 件候选</b>
      <small>以下为商品标价，确认运费后再比较总价。</small>
      ${pendingShipping.map(entry => `<div><a href="${escapeHtml(entry.detail_url)}" target="_blank" rel="noreferrer">${escapeHtml(entry.title)}</a><strong>${yuan(entry.item_price)} + 运费</strong><small>${platformLabel(entry)} · 采集 ${escapeHtml(shortDate(entry.observed_at))}</small>${blockListingButton(entry)}</div>`).join('')}
    </div>` : ''}
    ${pendingReview.length ? `<details class="shipping-pending"><summary>另有 ${pendingReview.length} 件待核实商品</summary>
      <small>系列、版本或成色尚未确认，暂不参与最低价排名。</small>
      ${pendingReview.map(entry => `<div><a href="${escapeHtml(entry.detail_url)}" target="_blank" rel="noreferrer">${escapeHtml(entry.title)}</a><strong>标价 ${yuan(entry.item_price)}</strong><small>${platformLabel(entry)} · ${escapeHtml(entry.reject_reason)} · 采集 ${escapeHtml(shortDate(entry.observed_at))}</small>${blockListingButton(entry)}</div>`).join('')}
    </details>` : ''}
    <div class="card-actions">${deltaHtml}${cardActions}</div>
  </article>`;
}

// 顶部横幅：把"最近一次侦察失败了"直接摆在用户眼前，
// 否则页面会继续把几天前的价格显示成"当前"。
function renderFreshnessBanner() {
  const box = $('#freshnessBanner');
  if (!box) return;
  const data = state.dashboard;
  const run = data.latest_run;
  if (!run) { box.className = 'freshness-banner'; box.innerHTML = ''; return; }

  const okRun = (data.runs || []).find(entry => entry.id === data.latest_successful_run_id);
  const dataDate = okRun ? shortDate(okRun.finished_at || okRun.started_at) : null;
  const label = { success: '成功', partial: '部分完成', failed: '失败', running: '进行中' }[run.status] || run.status;

  if (run.status === 'success') {
    box.className = 'freshness-banner';
    box.innerHTML = '';
    return;
  }
  if (run.status === 'running') {
    box.className = 'freshness-banner warn';
    box.innerHTML = '<div class="freshness-head"><b>正在搜索关注型号的新报价</b></div><p>请保留自动打开的采集窗口，搜索完成后页面会自动更新。现有报价仍显示各自的采集时间。</p>';
    return;
  }

  const failed = run.status === 'failed';
  const firstError = readableError(run.error).split('\n').filter(Boolean)[0] || '（没有记录原因）';
  const age = !failed ? '部分型号或平台未完成采集，请核对每条报价的采集时间；旧报价已标注为旧批次。'
    : dataDate ? `本次未更新报价，页面沿用 <b>${escapeHtml(dataDate)}</b> 的采集，请核对采集时间。`
    : '目前还没有任何一次成功的采集，页面上的价格为空是正常的。';

  box.className = `freshness-banner ${failed ? 'error' : 'warn'}`;
  box.innerHTML = `
    <div class="freshness-head"><b>${failed ? '最近一次侦察失败' : '最近一次侦察只完成了一部分'}</b>
    <span>${escapeHtml(shortDate(run.finished_at || run.started_at))} · 状态：${escapeHtml(label)}</span></div>
    <p>${age}</p>
    <p class="freshness-reason">失败原因：${escapeHtml(firstError)}</p>`;
}

function renderDashboard() {
  const data = state.dashboard;
  const active = data.items.filter(item => item.active);
  const purchased = data.items.filter(item => !item.active);
  $('#activeCount').textContent = active.length;
  $('#fxRate').textContent = data.fx.rate ? data.fx.rate.toFixed(5) : '—';
  $('#fxDate').textContent = renderFxNote(data.fx);
  const taskCount = Number(Boolean(data.task.scan_installed)) + Number(Boolean(data.task.dashboard_installed));
  $('#taskState').textContent = data.task.installed ? '已部署' : `${taskCount}/2`;
  renderFreshnessBanner();
  const progress = data.tracking_progress;
  $('#searchProgress').hidden = !data.tracking || !progress;
  $('#searchProgress').textContent = progress
    ? progress.item_name
      ? `正在搜索 ${progress.platform_label} · ${progress.item_name}（第 ${progress.item_index}/${progress.item_count} 款），本轮已读取 ${progress.listing_count} 条商品。`
      : `${progress.platform_label}，本轮已读取 ${progress.listing_count} 条商品。`
    : '';
  const marketStates = Object.fromEntries((data.marketplaces || []).map(entry => [entry.platform, entry]));
  $('#marketplaceStatus').innerHTML = `<span>屏蔽：旧 HG / 老 HG / 旧版 HG</span>` + ['xianyu', 'pdd'].map(platform => {
    const entry = marketStates[platform];
    const status = {running:'检查中',success:'已采集',partial:'部分完成',failed:'采集失败',skipped:'未启用'}[entry?.status] || '尚未采集';
    return `<span title="${escapeHtml(readableError(entry?.error))}">${platformLabel({platform})}：${status}${entry?.error ? ` · ${escapeHtml(readableError(entry.error).slice(0,120))}` : ''}</span>`;
  }).join('');
  $('#watchGrid').innerHTML = active.length ? active.map(renderCard).join('') : '<div class="empty">暂无关注型号，点击“新增关注”开始。</div>';
  $('#purchasedGrid').innerHTML = purchased.length ? purchased.map(renderCard).join('') : '<div class="empty">已购入的型号会出现在这里，并停止每周追踪。</div>';
  renderSpouseMeter(data.purchase_summary || {});
  renderSecretaryBriefing(active.length, data.latest_run, data.purchase_summary || {});
  renderRuns(data.runs);
  renderBlockedListings();
  renderStatus(data.latest_run, data.tracking);
  bindCardActions();
}

// 公布日期和获取时间分开。周末的最新工作日数据不等于网络失败缓存。
function renderFxNote(fx) {
  if (!fx || !fx.rate) return '等待搜索时更新汇率';
  const source = fx.source || '来源未知';
  const status = fx.stale === true ? `获取失败，沿用缓存${fx.checked_at ? ` · 尝试 ${shortDate(fx.checked_at)}` : ''}`
    : fx.fetched_at ? `已更新 ${shortDate(fx.fetched_at)}` : '上次保存汇率，搜索时更新';
  return `公布 ${fx.date || '日期未知'} · ${source} · ${status}`;
}

function renderSpouseMeter(summary) {
  const total = Number(summary.total_cny || 0);
  const level = summary.anger_level || (total > 500 ? 'rage' : total > 300 ? 'warning' : 'calm');
  const percent = Math.min(100, Math.max(3, total / 600 * 100));
  const labels = { calm: '审查通过', warning: '预算关注', rage: '雷霆审查' };
  const meter = $('#spouseMeter');
  meter.className = `spouse-meter ${level}`;
  $('#angerFill').style.height = `${percent}%`;
  $('#spouseMood').textContent = labels[level];
  $('#monthPurchaseTotal').textContent = yuan(total);
  const unknown = Number(summary.unknown_count || 0);
  $('#monthPurchaseCount').textContent = `本月 ${summary.item_count || 0} 架${unknown ? ` · ${unknown} 架待补价` : ''}`;
}

function renderSecretaryBriefing(activeCount, latestRun, purchaseSummary) {
  const total = Number(purchaseSummary.total_cny || 0);
  const level = purchaseSummary.anger_level || 'calm';
  let domestic = '家庭战线稳定，仍在安全预算内。';
  if (level === 'warning') domestic = '本月采购已突破 ¥300，顶头上司已经开始关注预算。';
  if (level === 'rage') domestic = '预算已越过 ¥500，顶头上司正在雷霆审查，请立即进入静默航行。';
  const scan = latestRun
    ? `上次侦察：${({success:'成功', partial:'部分完成', failed:'失败', running:'进行中'})[latestRun.status] || latestRun.status}。`
    : '首次市场侦察尚未执行。';
  $('#secretaryMessage').textContent = `总帅大人，本周秘书例会开始：当前锁定 ${activeCount} 架目标，本月已入库 ${yuan(total)}。${domestic}${scan}`;
}

function renderRuns(runs) {
  if (!runs.length) {
    $('#runsTable').innerHTML = '<div class="empty">尚无运行记录</div>';
    return;
  }
  const labels = { success: '成功', partial: '部分成功', failed: '失败', running: '运行中' };
  $('#runsTable').innerHTML = `<table><thead><tr><th>开始时间</th><th>状态</th><th>汇率</th><th>说明</th></tr></thead><tbody>${runs.map(run => `
    <tr><td>${formatDate(run.started_at)}</td><td><span class="status ${run.status}">${labels[run.status] || run.status}</span></td>
    <td>${run.fx_rate ? Number(run.fx_rate).toFixed(6) : '—'}<br><small>${escapeHtml(run.fx_source || '')}</small></td>
    <td>${escapeHtml(readableError(run.error) || '—').replaceAll('\n', '<br>')}</td></tr>`).join('')}</tbody></table>`;
}

function renderStatus(run, tracking) {
  const dot = $('#sideDot');
  dot.className = 'dot';
  $('#searchPlatform').disabled = state.connected === false || tracking || state.startingRun;
  if (state.connected === false) {
    dot.classList.add('failed');
    $('#sideStatus').textContent = '后台未连接';
    $('#sideTime').textContent = '请从桌面快捷方式启动';
    $('#runButton').disabled = true;
    $('#runButton').innerHTML = '<span>SCAN</span> 后台未连接';
    return;
  }
  if (tracking || state.startingRun) {
    dot.classList.add('running');
    $('#sideStatus').textContent = '正在检查';
    $('#sideTime').textContent = '请稍候，通常需要数分钟';
    $('#runButton').disabled = true;
    $('#runButton').innerHTML = '<span>SCAN</span> 搜索中…';
    return;
  }
  $('#runButton').disabled = false;
  $('#runButton').innerHTML = '<span>SCAN</span> 搜索全部关注';
  if (!run) return;
  dot.classList.add(run.status === 'success' ? 'ok' : run.status === 'failed' ? 'failed' : 'running');
  $('#sideStatus').textContent = ({success:'上次成功', partial:'上次部分成功', failed:'上次失败', running:'异常中断'})[run.status] || run.status;
  $('#sideTime').textContent = formatDate(run.finished_at || run.started_at);
}

function bindCardActions() {
  $$('.edit-button').forEach(button => button.onclick = () => openItemDialog(Number(button.dataset.id)));
  $$('.purchased-button').forEach(button => button.onclick = () => openPurchaseDialog(Number(button.dataset.id)));
  $$('.restore-button').forEach(button => button.onclick = () => restorePurchased(Number(button.dataset.id)));
  $$('.block-listing-button').forEach(button => button.onclick = () => hideListing(Number(button.dataset.listingId)));
  $$('.restore-listing-button').forEach(button => button.onclick = () => restoreListing(Number(button.dataset.blockId)));
}

function renderBlockedListings() {
  const entries = state.dashboard.blocked_listings || [];
  $('#blockedCount').textContent = `${entries.length} 件`;
  $('#blockedList').innerHTML = entries.length ? entries.map(entry => `
    <article class="blocked-entry">
      <div><small>${platformLabel(entry)} · 屏蔽于 ${escapeHtml(shortDate(entry.blocked_at))}</small>
      <a href="${escapeHtml(entry.detail_url)}" target="_blank" rel="noreferrer">${escapeHtml(entry.title)}</a></div>
      <button type="button" class="button secondary restore-listing-button" data-block-id="${entry.id}">恢复显示</button>
    </article>`).join('') : '<div class="empty">还没有屏蔽商品。结果旁的“删除并屏蔽”会把不想要的商品移到这里。</div>';
}

async function listingAction(key, path, method, message) {
  if (state.listingActions.has(key)) return;
  state.listingActions.add(key);
  try {
    await api(path, { method });
    await loadDashboard();
    toast(message);
  } catch (error) { toast(error.message, true); }
  finally { state.listingActions.delete(key); }
}

async function hideListing(id) {
  return listingAction(`block:${id}`, `/api/listings/${id}/block`, 'POST',
    '已删除并屏蔽，以后不再显示。误删可在左侧“已屏蔽”中恢复。');
}

async function restoreListing(id) {
  return listingAction(`restore:${id}`, `/api/blocked-listings/${id}`, 'DELETE', '已恢复显示，这条商品可以再次出现在结果中。');
}

async function loadDashboard() {
  state.dashboard = await api('/api/dashboard');
  renderDashboard();
}

async function loadSettings() {
  state.settings = await api('/api/settings');
  const form = $('#settingsForm');
  Object.entries(state.settings).forEach(([key, value]) => {
    const input = form.elements[key];
    if (!input) return;
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value ?? '';
  });
}

function openItemDialog(id = null) {
  const dialog = $('#itemDialog');
  const form = $('#itemForm');
  form.reset();
  form.elements.id.value = '';
  $('#itemDialogTitle').textContent = '新增关注';
  if (id) {
    const item = state.dashboard.items.find(row => row.id === id);
    if (!item) return;
    $('#itemDialogTitle').textContent = '编辑关注';
    form.elements.id.value = id;
    for (const key of ['name', 'model_code', 'search_query', 'msrp_jpy', 'official_url']) form.elements[key].value = item[key] ?? '';
    form.elements.aliases.value = (item.aliases || []).join('\n');
  }
  dialog.showModal();
}

async function saveItem(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const id = form.elements.id.value;
  const payload = {
    name: form.elements.name.value.trim(),
    model_code: form.elements.model_code.value.trim(),
    search_query: form.elements.search_query.value.trim(),
    aliases: form.elements.aliases.value.split(/\r?\n|,/).map(x => x.trim()).filter(Boolean),
    msrp_jpy: Number(form.elements.msrp_jpy.value || 0),
    official_url: form.elements.official_url.value.trim(),
  };
  try {
    const saved = await api(id ? `/api/items/${id}` : '/api/items', { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    $('#itemDialog').close();
    const message = id ? '关注型号已更新' : '已新增关注型号';
    toast(saved.item?.msrp_jpy > 0 ? message : `${message}；日元发售价待核实，可点编辑补充`);
    await loadDashboard();
  } catch (error) { toast(error.message, true); }
}

function openPurchaseDialog(id) {
  const item = state.dashboard.items.find(row => row.id === id);
  if (!item) return;
  const dialog = $('#purchaseDialog');
  const form = $('#purchaseForm');
  const suggested = item.summary?.current?.total_price ?? item.summary?.unknown_shipping?.item_price ?? '';
  form.reset();
  form.elements.item_id.value = id;
  form.elements.price_cny.value = suggested;
  $('#purchaseItemName').textContent = `${item.name}${item.model_code ? ` · ${item.model_code}` : ''}`;
  $('#purchasePriceHint').textContent = suggested === ''
    ? '当前没有可用价格，请填写你的实际成交总价。'
    : '已自动带入当前最低含运费价格，你可以按真实成交价修改。';
  dialog.showModal();
  requestAnimationFrame(() => form.elements.price_cny.focus());
}

async function savePurchase(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const id = Number(form.elements.item_id.value);
  const price = Number(form.elements.price_cny.value);
  if (!Number.isFinite(price) || price < 0) {
    toast('请填写有效的实际成交总价', true);
    return;
  }
  try {
    await api(`/api/items/${id}/purchased`, {
      method: 'POST',
      body: JSON.stringify({ purchased: true, price_cny: price }),
    });
    $('#purchaseDialog').close();
    toast(`机体已入库，本月采购累计计入 ${yuan(price)}`);
    await loadDashboard();
  } catch (error) { toast(error.message, true); }
}

async function restorePurchased(id) {
  if (!confirm('恢复关注后会撤销这笔已购入记录与对应的月度金额，确定吗？')) return;
  try {
    await api(`/api/items/${id}/purchased`, {
      method: 'POST',
      body: JSON.stringify({ purchased: false, price_cny: null }),
    });
    toast('已恢复关注，并撤销对应采购金额');
    await loadDashboard();
  } catch (error) { toast(error.message, true); }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    scan_weekday: form.elements.scan_weekday.value,
    scan_time: form.elements.scan_time.value,
    dashboard_time: form.elements.dashboard_time.value,
    destination_province: form.elements.destination_province.value.trim(),
    destination_city: form.elements.destination_city.value.trim(),
    max_results: Number(form.elements.max_results.value),
    detail_checks: Number(form.elements.detail_checks.value),
    headless: form.elements.headless.checked,
  };
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    toast('设置已保存');
    await loadSettings();
  } catch (error) { toast(error.message, true); }
}

async function runNow() {
  if (state.startingRun || state.dashboard?.tracking || state.connected === false) return;
  state.startingRun = true;
  renderStatus(state.dashboard?.latest_run, false);
  try {
    await api('/api/run', { method: 'POST', body: JSON.stringify({ platform: $('#searchPlatform').value }) });
    toast('搜索已开始，请保留自动打开的采集窗口，完成后页面会更新');
    startRunPolling();
    await loadDashboard();
  } catch (error) { toast(error.message, true); }
  finally {
    state.startingRun = false;
    renderStatus(state.dashboard?.latest_run, Boolean(state.dashboard?.tracking));
  }
}

function startRunPolling() {
  if (!state.runTimer) state.runTimer = setInterval(pollRun, 3000);
}

async function refreshConnection() {
  if (state.runTimer || state.refreshing) return;
  state.refreshing = true;
  try {
    await loadDashboard();
    if (state.dashboard.tracking) startRunPolling();
  } catch (error) {
    // The persistent connection banner explains the failure; the next refresh retries.
  } finally { state.refreshing = false; }
}

// 抓取失败有**两条**不同的路径，以前只看了其中一条：
//   1) 后台任务抛异常        -> status.error
//   2) 任务正常结束但业务失败 -> status.result.status === 'failed' + result.errors
// 只检查 status.error 会把第 2 种误报成"价格检查完成"。
async function pollRun() {
  if (state.polling) return;
  state.polling = true;
  const wasConnected = state.connected;
  try {
    const status = await api('/api/run/status');
    if (status.running) {
      await loadDashboard();
      state.dashboard.tracking = true;
      renderStatus(state.dashboard.latest_run, true);
      return;
    }
    clearInterval(state.runTimer);
    state.runTimer = null;

    const result = status.result;
    const labels = { success: '价格检查完成', partial: '价格检查部分完成', failed: '价格检查失败' };
    const causes = (result && result.causes || []).join(', ');
    const firstError = readableError((result && result.errors || [])[0] || status.error || '');
    const message = status.error
      ? `价格检查失败：${status.error}`
      : labels[result && result.status]
        ? `${labels[result.status]}${result.listing_count !== undefined ? `（本次采集 ${result.listing_count} 条）` : ''}${firstError ? ` · ${firstError}` : ''}`
        : '价格检查已结束';
    const isBad = Boolean(status.error) || (result && result.status !== 'success');
    toast(message, isBad);
    if (causes) console.warn('价格检查失败分类：', causes);
    await loadDashboard();
  } catch (error) {
    if (wasConnected !== false) toast(error.message, true);
  } finally { state.polling = false; }
}

function switchView(view) {
  state.view = view;
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $$('.view').forEach(section => section.classList.remove('active'));
  $(`#${view}View`).classList.add('active');
  const copy = {
    watch: ['我的关注', '全新未拼 // 非预售 // 含运费价格'],
    purchased: ['已购入', '机体已完成采购，监测任务暂停，可随时重新列入目标。'],
    runs: ['运行记录', '查看每次侦察、汇率来源与系统异常。'],
    blocked: ['已屏蔽商品', '已从报价、排名和后续搜索结果中移除，可随时恢复显示。'],
    settings: ['自动任务', '配置搜索和网页开启时间，查看运费说明。'],
    themes: ['外观皮肤', '选择战情室配色。'],
  }[view];
  $('#pageTitle').textContent = copy[0];
  $('#pageSubtitle').textContent = copy[1];
  const showActions = view === 'watch';
  $('#addButton').style.display = showActions ? '' : 'none';
  $('#runButton').style.display = showActions ? '' : 'none';
  $('#searchPlatform').style.display = showActions ? '' : 'none';
  $('#loginButton').style.display = showActions ? '' : 'none';
  $('#pddLoginButton').style.display = showActions ? '' : 'none';
}

async function simpleAction(path, success) {
  try { const result = await api(path, { method: 'POST' }); toast(result.message || success); await loadDashboard(); }
  catch (error) { toast(error.message, true); }
}

document.addEventListener('DOMContentLoaded', async () => {
  $$('.nav-item').forEach(button => button.onclick = () => switchView(button.dataset.view));
  $('#addButton').onclick = () => openItemDialog();
  $('#runButton').onclick = runNow;
  $('#reconnectButton').onclick = async () => {
    try {
      await Promise.all([loadDashboard(), loadSettings()]);
      if (state.dashboard.tracking) startRunPolling();
      toast('网站后台已连接，可以开始搜索');
    } catch (error) { toast(error.message, true); }
  };
  $('#loginButton').onclick = () => simpleAction('/api/login-window', '登录窗口已打开');
  $('#pddLoginButton').onclick = () => simpleAction('/api/pdd/login-window', '拼多多登录窗口已打开');
  $('#scheduleButton').onclick = () => simpleAction('/api/schedule/install', '周一双任务已部署');
  $('#itemForm').addEventListener('submit', saveItem);
  $('#purchaseForm').addEventListener('submit', savePurchase);
  $('#settingsForm').addEventListener('submit', saveSettings);
  $$('.close-dialog').forEach(button => button.onclick = () => $('#itemDialog').close());
  $$('.close-purchase-dialog').forEach(button => button.onclick = () => $('#purchaseDialog').close());
  setInterval(refreshConnection, 15000);
  try {
    await Promise.all([loadDashboard(), loadSettings()]);
    if (state.dashboard.tracking) startRunPolling();
  } catch (error) { toast(error.message, true); }
});
