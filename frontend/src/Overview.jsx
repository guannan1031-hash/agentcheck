import React from 'react';
import './overview.css';

const capabilities=[
  {number:'01',title:'联系上下文做判断',text:'把客户多轮描述整理成已知事实、待确认项和风险信号；信息变化后重新判断，不沿用失效结论。',tag:'客服少翻记录'},
  {number:'02',title:'规则与依据可核对',text:'回答关联知识版本、业务规则和事实来源。缺少库存、订单或物流结果时保持未知，先追问或查询。',tag:'减少拍脑袋回复'},
  {number:'03',title:'一次案件，多部门闭环',text:'客服可拆出仓库、供应链、财务、质量等任务；主管逐单、批量或按预授权规则审批，各部门独立回传。',tag:'减少漏单催办'},
  {number:'04',title:'高风险立即交给人',text:'退款赔付、食品安全、身体不适、投诉和隐私变更默认人工处理；AI 不能扩大商家授权。',tag:'控制经营风险'},
];

const demoSteps=[
  {time:'00:50',title:'AI 草稿有依据',text:'载入合成商品咨询，选择智谱生成草稿，再展示知识来源和价格计算。',page:'desk',action:'开始第一段'},
  {time:'00:45',title:'高风险交给人工',text:'用破损咨询展示证据不足、食品安全或赔付风险如何被拦下并转人工。',page:'damage',action:'开始第二段'},
  {time:'00:45',title:'问题进入协作闭环',text:'展示客服提交、主管审批和部门回传，说明问题不会停在聊天窗口。',page:'collaboration',action:'开始第三段'},
  {time:'00:40',title:'最后看覆盖范围',text:'打开全场景中心，展示 218 个已登记场景与规则待业务确认的状态。',page:'scenarios',action:'查看场景中心'},
];

export default function Overview({onNavigate,scenarioCount=218}){
  return <div className="overview">
    <section className="overview-hero">
      <div className="overview-hero-copy">
        <span className="overview-kicker">ECOMMERCE SERVICE OPERATIONS AGENT · V1.7</span>
        <h1>从“会回复”走到<br/><em>把客户问题办完</em></h1>
        <p>面向食品与日用品电商团队，把客服上下文推理、知识依据、人工审批和跨部门任务放进一个可追踪的案件流程。</p>
        <div className="overview-actions">
          <button className="overview-primary" onClick={()=>onNavigate('desk')}>开始 3 分钟录制演示</button>
          <button className="overview-secondary" onClick={()=>onNavigate('collaboration')}>查看协作工单</button>
        </div>
        <p className="overview-caption">当前为可运行本地试点版：消费者回复和业务系统写入保持关闭；部门通知需主管配置并手动发送。</p>
      </div>
      <div className="overview-proof" aria-label="当前验证状态">
        <div><strong>{scenarioCount}</strong><span>个已登记业务场景</span></div>
        <div><strong>93</strong><span>项后端自动化测试</span></div>
        <div><strong>5</strong><span>组核查流程包</span></div>
        <div className="overview-proof-state"><i/>本地流程已验证</div>
      </div>
    </section>

    <section className="overview-section overview-problem">
      <div className="overview-section-head">
        <span>WHY PAY</span>
        <h2>老板付费买的是更少的人工操作、更少的漏单和更可控的风险</h2>
      </div>
      <p className="overview-lead">知识库只能帮助客服找到答案。真正昂贵的是：反复读上下文、确认规则、找主管审批、催多个部门、再回来告诉客户结果。这个产品把这些动作串成可审核的闭环。</p>
      <div className="overview-capabilities">{capabilities.map(item=><article key={item.number}>
        <span className="overview-number">{item.number}</span><h3>{item.title}</h3><p>{item.text}</p><b>{item.tag}</b>
      </article>)}</div>
    </section>

    <section className="overview-section">
      <div className="overview-section-head">
        <span>RECORDING FLOW</span><h2>按一条客户问题完成 3 分钟录制演示</h2>
      </div>
      <div className="overview-demo-grid">{demoSteps.map((step,index)=><article key={step.title}>
        <div className="overview-step-top"><span>{step.time}</span><b>0{index+1}</b></div>
        <h3>{step.title}</h3><p>{step.text}</p>
        <button onClick={()=>onNavigate(step.page)}>{step.action} →</button>
      </article>)}</div>
    </section>

    <section className="overview-section overview-commercial">
      <div className="overview-commercial-copy">
        <span className="overview-kicker">PAID PILOT</span>
        <h2>先卖一个可验收的小闭环</h2>
        <p>首单限定一个店铺、一个咨询入口和一组高频场景。先记录人工处理基线，再比较试点后的主动操作时间、错分派、漏结案和越权执行。</p>
        <div className="overview-commercial-model">
          <div><b>一次性实施费</b><span>流程诊断、规则整理、知识配置与一个连接器联调</span></div>
          <div><b>按店/月服务费</b><span>运行维护、规则更新、失败排查和效果复盘</span></div>
          <div><b>单独列示成本</b><span>模型调用、平台接口和第三方服务按实际范围核算</span></div>
        </div>
      </div>
      <aside>
        <span>试点验收建议</span>
        <strong>同类案件 ≥ 30</strong>
        <ul>
          <li>主动操作时间中位数</li>
          <li>待办超时与漏结案数</li>
          <li>错误分派与重复处理数</li>
          <li>未经授权自动执行数 = 0</li>
        </ul>
        <button onClick={()=>onNavigate('status')}>查看运行与审计</button>
      </aside>
    </section>

    <section className="overview-boundary">
      <div><span>已能展示</span><p>场景判断、缺项追问、多部门审批与回传、通知预览和连接器可靠性状态、SQLite 本地持久化。</p></div>
      <div><span>付费试点前完成</span><p>取得商家授权，配置一个真实部门目标，并完成送达、限流、故障恢复与生产验收。</p></div>
      <div><span>始终由人负责</span><p>退款赔付、食品安全、法律投诉、敏感信息与超出预授权范围的决定。</p></div>
    </section>
  </div>;
}
