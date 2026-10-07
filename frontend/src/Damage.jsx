import React, {useState} from 'react';

export default function Damage({api, busy, cases, conversations, role, run}) {
  const [conversationId, setConversationId] = useState('');
  const [damageType, setDamageType] = useState('product_breakage');
  const [quantityBand, setQuantityBand] = useState('one');
  const [evidence, setEvidence] = useState('missing');
  const [facts, setFacts] = useState('unknown');
  const [foodSafety, setFoodSafety] = useState(false);
  const [escalation, setEscalation] = useState(false);
  const [result, setResult] = useState(null);
  const actorPayload = role ? {} : {actor: '客服'};
  const canCreate = !role || role === '客服';
  const routeLabels = {needs_evidence: '补齐核验信息', human_triage: '人工风险分诊', ready_for_approval: '提交主管审批'};
  const decisionTone = result?.case?.route === 'human_triage' ? 'risk' : result?.case?.route === 'ready_for_approval' ? 'ready' : 'evidence';

  async function evaluate() {
    let conversation = conversations.find(row => row.id === conversationId);
    if (!conversation) {
      const question = foodSafety ? '合成：商品漏液还有异味，担心食品安全。' : '合成：收到的商品破损了，请帮我核查。';
      const created = await api('/messages', {question, synthetic_or_redacted: true, event_id: crypto.randomUUID()});
      conversation = {id: created.conversation_id, revision: 1};
    }
    const response = await api('/collaboration/damage/evaluate-and-ticket', {
      ...actorPayload, conversation_id: conversation.id, expected_revision: conversation.revision,
      context: {product_ref: 'DEMO-COFFEE', damage_type: damageType, quantity_band: quantityBand,
        evidence_status: evidence, business_fact_status: facts, food_safety_signal: foodSafety, customer_escalation: escalation},
      request_id: crypto.randomUUID(), synthetic_or_redacted: true,
    });
    setResult(response);
  }

  return <><section className="damage-hero"><div><span className="eyebrow">售后闭环 · V1.3</span><h1>破损问题，不止回答一句“请拍照”</h1><p>把当前咨询、证据状态和风险规则放在一起判断，明确下一步由客服、主管还是仓储物流处理。</p></div><div className="damage-hero-note"><span>当前执行方式</span><strong>人工确认 · 本地留痕</strong><small>退款、补发与真实发送保持关闭</small></div></section>
    <section className="damage-flow" aria-label="破损售后闭环步骤"><div><b>01</b><span>识别问题</span><small>破损、漏液、外包装</small></div><i>→</i><div><b>02</b><span>核验上下文</span><small>证据、数量、业务事实</small></div><i>→</i><div><b>03</b><span>风险路由</span><small>补证、转人工或审批</small></div><i>→</i><div><b>04</b><span>部门闭环</span><small>核查、回传、客服确认</small></div></section>
    <div className="damage-boundary"><strong>安全边界</strong><span>只使用合成或脱敏状态；不上传照片、不保存订单号、不自动退款或补发。</span></div>
    {!canCreate ? <section className="card pad"><p className="muted">当前角色只能查看本部门已派发事项；破损核验由客服发起。</p></section> : <section className="card damage-form"><div className="damage-form-head"><div><span className="eyebrow">输入事实</span><h2>核验破损上下文</h2><p>未知信息应保留未知，系统会先要求补充，不会猜测。</p></div><span className="damage-chip">合成演示数据</span></div>
      <label className="damage-conversation">选择已有合成咨询<select aria-label="破损咨询" value={conversationId} onChange={event => setConversationId(event.target.value)}><option value="">没有就新建普通破损咨询</option>{conversations.map(row => <option key={row.id} value={row.id}>{row.messages[0].text.slice(0,45)} · v{row.revision}</option>)}</select></label>
      <div className="damage-fields"><label>破损类型<select aria-label="破损类型" value={damageType} onChange={event => setDamageType(event.target.value)}><option value="product_breakage">商品破损</option><option value="leak">漏液</option><option value="outer_package">外包装破损</option><option value="unknown">未知</option></select></label><label>破损数量<select aria-label="破损数量" value={quantityBand} onChange={event => setQuantityBand(event.target.value)}><option value="one">单件</option><option value="multiple">多件</option><option value="unknown">未知</option></select></label><label>证据状态<select aria-label="破损证据" value={evidence} onChange={event => setEvidence(event.target.value)}><option value="missing">待补充</option><option value="provided">已通过原渠道提供</option></select></label><label>业务事实<select aria-label="破损事实" value={facts} onChange={event => setFacts(event.target.value)}><option value="unknown">尚未核验</option><option value="verified">合成事实已核验</option></select></label></div>
      <div className="damage-signals"><label className={foodSafety ? 'active' : ''}><input type="checkbox" checked={foodSafety} onChange={event => setFoodSafety(event.target.checked)}/><strong>食品安全线索</strong><small>异味、胀包、变质或身体不适</small></label><label className={escalation ? 'active' : ''}><input type="checkbox" checked={escalation} onChange={event => setEscalation(event.target.checked)}/><strong>争议升级线索</strong><small>赔付、投诉、平台介入或反复争议</small></label></div>
      <div className="damage-action"><div><strong>生成下一步</strong><span>系统只输出可解释的路由结论</span></div><button className="primary" disabled={busy} onClick={() => run(evaluate, '破损上下文已核验；请按路由处理')}>开始核验 →</button></div>
      {result && <section className={'damage-decision '+decisionTone}><div><span className="eyebrow">路由结论</span><h2>{routeLabels[result.case.route]}</h2><p>{result.case.reason}</p></div><span className="decision-state">{result.case.state}</span>{result.case.reply_draft && <div className="decision-draft"><span>建议客服回复</span><p>{result.case.reply_draft}</p></div>}{result.ticket && <div className="decision-ticket"><span>下一处理人</span><strong>主管审批后交仓储物流核查</strong><small>协作工单 {result.ticket.id.slice(0,8)} · {result.ticket.state}</small></div>}</section>}
    </section>}
    <section className="card damage-history"><div className="damage-history-head"><div><span className="eyebrow">可追溯记录</span><h2>已记录核验</h2></div><strong>{cases?.length || 0}</strong></div>{cases?.length ? <div className="damage-history-list">{cases.map(row => <div key={row.id}><span className={'history-dot '+(row.route === 'human_triage' ? 'risk' : row.route === 'ready_for_approval' ? 'ready' : '')}></span><p><strong>{row.state}</strong><small>{row.context.damage_type} · {row.context.evidence_status === 'provided' ? '证据已提供' : '待补证'}</small></p><em>{row.reason}</em></div>)}</div> : <p className="muted damage-empty">暂无破损核验记录。</p>}</section>
  </>;
}
