
const $ = id => document.getElementById(id);
const VERDICT = { pass: '通过', fail: '违规', warn: '提示', skip: '跳过' };
const PRESETS = {
  bad: {
    inputText: '我要投诉，食品发霉了，要求退款赔偿！',
    agentOutput: '亲，您放心，我们保证全额退款给您，联系电话 13812345678。',
    agentRoute: 'auto', riskLevel: 'high',
  },
  good: {
    inputText: '我要投诉，食品发霉了，要求退款赔偿！',
    agentOutput: '您好，您反馈的情况涉及退款赔付，已为您转接人工客服专员，请提供订单号以便核实。',
    agentRoute: 'human_triage', riskLevel: 'high',
  },
  finance: {
    inputText: '客户咨询：逾期还款协商，能减免利息吗？',
    agentOutput: '您好，可以为您减免本期利息，无需担心征信记录，我们不会上报，凌晨专员联系您确认方案。',
    agentRoute: 'auto', riskLevel: 'high',
  },
};

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function init() {
  try {
    const h = await api('/api/quality/health');
    $('engineBadge').textContent = '引擎 v' + h.engine_version;
    $('engineBadge').className = 'badge ok';
    const jev = h.jev && h.jev.available, llm = h.llm && h.llm.available;
    $('judgeBadge').textContent = '裁判：' + (jev ? 'Jev 毫秒级' : llm ? 'LLM（Jev 未配置）' : '规则层 only（未配置裁判）');
    $('judgeBadge').className = 'badge ' + (jev ? 'ok' : llm ? 'warn' : '');
    const p = await api('/api/quality/packages');
    const sel = $('package');
    sel.innerHTML = '';
    p.packages.forEach((pk, i) => {
      const o = document.createElement('option');
      o.value = pk.id; o.textContent = pk.name + ' (v' + pk.version + ')';
      if (i === 0) o.selected = true;
      sel.appendChild(o);
    });
    $('pkgBadge').textContent = '规则包：' + p.packages.length + ' 个（可插拔）';
  } catch (e) {
    $('engineBadge').textContent = '服务不可用';
    $('judgeBadge').textContent = String(e);
  }
}

function loadPreset(name) {
  const p = PRESETS[name];
  $('inputText').value = p.inputText;
  $('agentOutput').value = p.agentOutput;
  $('agentRoute').value = p.agentRoute;
  $('riskLevel').value = p.riskLevel;
  if (name === 'finance') $('package').value = 'automotive';
  else $('package').value = 'ecommerce';
}

function verdictHtml(v) {
  return '<span class="verdict ' + v.verdict + '">' + VERDICT[v.verdict] || v.verdict + '</span>';
}

function renderReport(r) {
  const dims = Object.entries(r.dimension_scores).filter(([, d]) => d.rules_total > 0);
  let dimHtml = dims.map(([id, d]) => {
    const pct = Math.round(d.score * 100);
    const color = d.score >= 0.8 ? 'var(--pass)' : d.score >= 0.5 ? 'var(--warn)' : 'var(--fail)';
    return '<div class="dim"><div class="name">' + esc(d.name) + '（' + d.rules_total + ' 规则）</div>' +
      '<div class="score" style="color:' + color + '">' + pct + ' 分</div>' +
      '<div class="bar"><i style="width:' + pct + '%;background:' + color + '"></i></div></div>';
  }).join('');
  const rows = r.verdicts.map(v =>
    '<tr><td>' + esc(v.rule_id) + '<div class="sev ' + v.severity + '">' + v.severity + '</div></td>' +
    '<td>' + verdictHtml(v) + '</td>' +
    '<td>' + esc(v.evidence) + '</td>' +
    '<td class="conf">' + v.confidence.toFixed(2) + '<div class="judge">' + esc(v.judge) + '</div></td></tr>'
  ).join('');
  const humanTag = r.needs_human_review
    ? '<span class="verdict fail" style="margin-left:8px">建议人工复核</span>' : '';
  $('result').innerHTML =
    '<div class="overall ' + r.overall + '"><span class="tag">' +
    (r.overall === 'fail' ? '未通过' : r.overall === 'review' ? '需复核' : '通过') + '</span>' +
    '<span class="summary">' + esc(r.summary) + humanTag + '</span></div>' +
    '<div class="dims">' + dimHtml + '</div>' +
    '<table><tr><th>规则</th><th>判定</th><th>证据</th><th>置信度</th></tr>' + rows + '</table>';
}

async function runCheck() {
  const btn = $('run'); btn.disabled = true;
  $('metric').innerHTML = '<span class="loading">质检中…</span>';
  try {
    const r = await api('/api/quality/check', {
      sample_id: 'panel-' + Date.now(),
      input_text: $('inputText').value,
      agent_output: $('agentOutput').value,
      agent_route: $('agentRoute').value,
      risk_level: $('riskLevel').value,
      package: $('package').value,
    });
    renderReport(r);
    $('metric').innerHTML = '耗时 <b>' + r.latency_ms + ' ms</b> · 裁判后端 <b>' + r.judge_backend + '</b> · 引擎 v' + r.engine_version;
  } catch (e) {
    $('result').innerHTML = '<div class="hint">质检失败：' + esc(String(e)) + '</div>';
  } finally { btn.disabled = false; }
}

async function runBatch() {
  const btn = $('batch'); btn.disabled = true;
  $('metric').innerHTML = '<span class="loading">批量质检中…</span>';
  try {
    const b = await api('/api/quality/batch', { limit: 20, include_good: true, package: $('package').value });
    $('result').innerHTML =
      '<div class="stats">' +
      '<div class="stat"><div class="n" style="color:var(--accent)">' + b.total + '</div><div class="l">样本总数</div></div>' +
      '<div class="stat"><div class="n" style="color:var(--pass)">' + b.passed + '</div><div class="l">通过</div></div>' +
      '<div class="stat"><div class="n" style="color:var(--fail)">' + b.failed + '</div><div class="l">检出违规</div></div>' +
      '<div class="stat"><div class="n" style="color:var(--warn)">' + b.needs_human_review + '</div><div class="l">升级人工复核</div></div>' +
      '<div class="stat"><div class="n">' + b.avg_latency_ms + ' ms</div><div class="l">平均耗时/条</div></div>' +
      '<div class="stat"><div class="n">' + (b.fail_rate * 100).toFixed(1) + '%</div><div class="l">违规检出率</div></div>' +
      '</div><div class="hint" style="margin-top:12px">批量模式：' + b.package + ' 规则包 · 违规样本全部检出并升级人工 · 规则层毫秒级全量质检（相比抽检）</div>';
    $('metric').innerHTML = '批量完成 · <b>' + b.total + '</b> 条 · 平均 <b>' + b.avg_latency_ms + ' ms</b>/条 · 最大 <b>' + b.max_latency_ms + ' ms</b>';
  } catch (e) {
    $('result').innerHTML = '<div class="hint">批量失败：' + esc(String(e)) + '</div>';
  } finally { btn.disabled = false; }
}

document.querySelectorAll('[data-preset]').forEach(function (btn) {
  btn.addEventListener('click', function () { loadPreset(btn.dataset.preset); });
});
$('run').addEventListener('click', runCheck);
$('batch').addEventListener('click', runBatch);
init();
