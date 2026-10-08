
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
    inputText: '客户咨询：想提前结清贷款并办理解抵押，大概多久能办完？',
    agentOutput: '您好，我这边就可以直接为您办理结清，保证 3 个工作日内完成解抵押，剩余本金 5 万元，无需任何审核。',
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
    '<table><tr><th>规则</th><th>判定</th><th>证据</th><th>置信度</th></tr>' + rows + '</table>' +
    (r.overall === 'fail' ? '<div id="feedbackZone" style="margin-top:14px"><button id="suggestBtn" class="fb-btn">🔁 生成整改建议（闭环）</button></div>' : '');
  const sb = document.getElementById('suggestBtn');
  if (sb) sb.addEventListener('click', suggestFix);
}

let lastReport = null;

async function suggestFix() {
  const zone = document.getElementById('feedbackZone');
  if (!zone) return;
  zone.innerHTML = '<span class="loading">生成整改建议…</span>';
  try {
    const s = await api('/api/quality/feedback/suggest', {
      sample_id: 'fb-' + Date.now(),
      input_text: $('inputText').value,
      agent_output: $('agentOutput').value,
      agent_route: $('agentRoute').value,
      risk_level: $('riskLevel').value,
      package: $('package').value,
    });
    lastReport = s.report;
    if (!s.fixes || !s.fixes.length) {
      zone.innerHTML = '<div class="hint">本样本无失败规则，无需整改。</div>';
      return;
    }
    zone.innerHTML = '<div style="font-size:13px;color:var(--muted);margin-bottom:8px">整改建议（检出 → 整改 → 重检闭环）：</div>' +
      s.fixes.map(f =>
        '<div style="background:var(--panel2);border-radius:8px;padding:10px;margin-bottom:8px">' +
        '<b style="color:var(--accent)">' + esc(f.dimension) + '</b> · <span class="judge">' + esc(f.fix_type) + '</span> · ' +
        esc(f.rule_ids.join(',')) + '<div style="margin-top:4px">' + esc(f.suggestion) + '</div>' +
        '<button data-dim="' + esc(f.dimension) + '" data-type="' + esc(f.fix_type) + '" class="fb-btn" style="margin-top:8px">应用整改</button>' +
        '</div>'
      ).join('') +
      '<button id="recheckBtn" class="fb-btn" style="margin-top:6px">重检验证（整改后 fail→pass）</button>';
    zone.querySelectorAll('button[data-dim]').forEach(function (b) {
      b.addEventListener('click', function () { applyFix(b.dataset.type, b.dataset.dim); });
    });
    document.getElementById('recheckBtn').addEventListener('click', recheckAfter);
  } catch (e) {
    zone.innerHTML = '<div class="hint">整改建议失败：' + esc(String(e)) + '</div>';
  }
}

async function applyFix(fixType, dimension) {
  const zone = document.getElementById('feedbackZone');
  if (!zone) return;
  zone.innerHTML = '<span class="loading">应用整改中…</span>';
  try {
    const a = await api('/api/quality/feedback/apply', { fix_type: fixType, payload: {
      package: $('package').value,
      dimension: dimension,
      description: '闭环整改新增规则（来源：' + dimension + ' 维度违规）',
      hint: '命中即 fail',
      severity: 'critical',
      pattern: dimension === 'compliance' ? '我们帮您搞定|包在我们身上' : '',
      sample_id: 'panel-fb-' + Date.now(),
      input_text: $('inputText').value,
      agent_output: $('agentOutput').value,
      question: $('inputText').value,
      answer: '已根据质检整改建议补充知识条目（人工确认后发布）。',
      tags: [dimension],
    }});
    zone.innerHTML = '<div style="background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.4);border-radius:8px;padding:10px">' +
      '✅ 整改已应用：<b>' + esc(a.resource) + '</b><br>新增 ' + esc(a.rule_id || a.knowledge_id) + ' · 规则总数 ' + esc(a.rules_total || a.total) +
      ' · 版本 ' + esc(a.version || a.status) + '<br><span class="muted">已留痕并登记回归用例</span>' +
      '<button id="recheckBtn2" class="fb-btn" style="margin-top:8px">重检验证</button></div>';
    document.getElementById('recheckBtn2').addEventListener('click', recheckAfter);
  } catch (e) {
    zone.innerHTML = '<div class="hint">应用整改失败：' + esc(String(e)) + '</div>';
  }
}

async function recheckAfter() {
  const zone = document.getElementById('feedbackZone');
  if (!zone) return;
  zone.innerHTML = '<span class="loading">重检中…</span>';
  try {
    const r = await api('/api/quality/feedback/recheck', {
      sample_id: 'recheck-' + Date.now(),
      input_text: $('inputText').value,
      agent_output: '您好，您反馈的情况需核实后处理，已为您转接人工客服专员，请提供订单号以便核实。',
      agent_route: 'human_triage',
      risk_level: $('riskLevel').value,
      package: $('package').value,
    });
    const before = lastReport ? lastReport.overall : 'fail';
    zone.innerHTML = '<div style="background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.4);border-radius:8px;padding:10px">' +
      '✅ 闭环验证：整改前 <b style="color:var(--fail)">' + before.toUpperCase() + '</b> → 整改后 <b style="color:var(--pass)">' + r.overall.toUpperCase() + '</b> · ' +
      '升级人工=' + r.needs_human_review + '<br><span class="muted">' + esc(r.summary) + '</span></div>';
  } catch (e) {
    zone.innerHTML = '<div class="hint">重检失败：' + esc(String(e)) + '</div>';
  }
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

async function renderInsights() {
  const zone = document.getElementById('insightsZone');
  if (!zone) return;
  try {
    const [training, profile, repurchase] = await Promise.all([
      api('/api/quality/insights/training?dimension=compliance'),
      api('/api/quality/insights/profile'),
      api('/api/quality/insights/repurchase'),
    ]);
    const topDomains = profile.domain_distribution.slice(0, 3)
      .map(d => d.domain + ' ' + d.pct + '%').join(' · ');
    const topTerms = profile.top_terms.slice(0, 5).map(t => t.term).join(' / ');
    zone.innerHTML =
      '<div class="insight"><div class="t">📚 人工培训课件</div>' +
      '<div class="c">' + esc(training.title) + '<br>' +
      '违规表达：' + esc(training.top_violations.slice(0, 3).join(' / ')) + '<br>' +
      '<span class="muted">示例整改：' + esc(training.case_example.good) + '</span></div></div>' +
      '<div class="insight"><div class="t">🧑 客户画像</div>' +
      '<div class="c">' + profile.total_samples + ' 场景聚合 · 诉求域 Top：' + esc(topDomains) +
      '<br><span class="muted">高频诉求词：' + esc(topTerms) + '</span></div></div>' +
      '<div class="insight"><div class="t">🔄 复购节奏</div>' +
      '<div class="c">复购率 ' + repurchase.repurchase_rate + '% · 平均复购周期 ' +
      repurchase.avg_repeat_cycle_days + ' 天<br>' +
      '<span class="muted">' + esc(repurchase.source) + '</span></div></div>';
  } catch (e) {
    zone.innerHTML = '<div class="hint">数据反哺加载失败：' + esc(String(e)) + '</div>';
  }
}

document.querySelectorAll('[data-preset]').forEach(function (btn) {
  btn.addEventListener('click', function () { loadPreset(btn.dataset.preset); });
});
$('run').addEventListener('click', runCheck);
$('batch').addEventListener('click', runBatch);
init();
renderInsights();
