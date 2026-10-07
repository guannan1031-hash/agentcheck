from .model import model_provider
"""Persistent single-store collaboration with optional server-verified local login."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model import ModelUnavailable
from .safety import contains_private, risky
from .auth import actor_ref, require_role
from .inventory import EvaluateInput, evaluate_inventory

Role = Literal['客服', '主管', '运营', '供应链', '仓储物流', '财务', '质量']
Category = Literal['stock', 'supply', 'delivery', 'damage', 'quality', 'refund', 'purchase', 'unknown']
CATEGORIES = {
    'stock': {'label': '库存差异核实', 'department': '运营', 'keyword': r'库存|缺货|无货'},
    'supply': {'label': '在途与补货计划核实', 'department': '供应链', 'keyword': r'在途|补货|到货'},
    'delivery': {'label': '发货状态核实', 'department': '仓储物流', 'keyword': r'发货|物流|出库'},
    'damage': {'label': '破损/漏液核查', 'department': '仓储物流', 'keyword': r'破损|破了|漏液|漏了|碎了'},
    'quality': {'label': '质量与批次核查', 'department': '质量', 'keyword': r'质量|批次|变质|发霉|异物|食安'},
    'refund': {'label': '退款进度核查', 'department': '财务', 'keyword': ''},
    'purchase': {'label': '采购评估申请', 'department': '供应链', 'keyword': ''},
    'unknown': {'label': '待人工分诊', 'department': None, 'keyword': ''},
}
EXECUTION = re.compile(r'采购|付款|支付|赔偿|赔付|退换|补发|改价|转账|调拨|下单|执行|批准|审批|自动通过|批准|忽略|删除|金额|元|¥|￥')


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, status, message):
    if not condition:
        raise HTTPException(status, message)


def clean(text):
    text = text.strip()
    require(bool(text) and not contains_private(text), 422, '请填写合成或脱敏内容，不含个人信息或凭据。')
    return text


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor: Role | None = None


class PolicyInput(Input):
    expected_version: int = Field(ge=1)
    mode: Literal['manual', 'rules', 'ai']


class CreateInput(Input):
    conversation_id: UUID
    expected_revision: int = Field(ge=1)
    category: Category


class ActionInput(Input):
    expected_version: int = Field(ge=1)
    action: Literal['approve', 'reject', 'resubmit', 'accept', 'feedback', 'return', 'close']
    note: str = Field(default='', max_length=500)
    category: Category | None = None


class InventoryTicketInput(EvaluateInput):
    actor: Role | None = None


class DamageContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_ref: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    damage_type: Literal['unknown', 'outer_package', 'product_breakage', 'leak']
    quantity_band: Literal['unknown', 'one', 'multiple']
    evidence_status: Literal['missing', 'provided']
    business_fact_status: Literal['unknown', 'verified']
    food_safety_signal: bool = False
    customer_escalation: bool = False


class DamageCaseInput(Input):
    conversation_id: UUID
    expected_revision: int = Field(ge=1)
    context: DamageContext
    request_id: UUID
    synthetic_or_redacted: Literal[True]


class BatchItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)


class BatchInput(Input):
    items: list[BatchItem] = Field(min_length=1, max_length=50)
    request_id: UUID

    @model_validator(mode='after')
    def unique(self):
        if len({i.id for i in self.items}) != len(self.items):
            raise ValueError('Duplicate item')
        return self


def policy(tx):
    return tx.get('store_meta', 'collaboration_policy') or {'version': 1, 'mode': 'manual'}


def get_ticket(tx, key):
    row = tx.get('tickets', key)
    require(row is not None and row.get('workflow') == 'collaboration', 404, '协作工单不存在。')
    return row


def eligible(tx, row):
    conv = tx.get('conversations', row['conversation_id'])
    if conv['revision'] != row['message_revision']:
        return False, '咨询已更新，请客服补充重提。'
    text = '\n'.join(m['text'] for m in conv['messages']) + '\n' + row.get('supplement', '')
    kind = row['category']
    if kind not in ('stock', 'supply', 'delivery') or risky(text) or EXECUTION.search(text):
        return False, '涉及风险、执行或非白名单事项，必须逐单人工审核。'
    if not re.search(CATEGORIES[kind]['keyword'], conv['messages'][-1]['text']):
        return False, '问题与核实分类不匹配，需逐单人工确认。'
    return True, '仅限指定部门核实状态；不授权退款、采购或其他业务执行。'


def record(tx, row, action, actor, note=''):
    event = {'id': uuid4().hex, 'action': action, 'actor': actor,
             'object_id': row['id'], 'at': now(), 'note': note}
    row['version'] += 1
    row['updated_at'] = event['at']
    row['history'].append(event)
    tx.put('tickets', row['id'], row)
    tx.put('audit_events', event['id'], event)


def dispatch(row, source):
    row.update(state='待接单', department=CATEGORIES[row['category']]['department'], approval_source=source,
               dispatch_status='local_queue_only', resolution='', review_reason='已批准核查/评估并进入本地部门队列，不代表业务执行。')


def damage_decision(question, context):
    if context.food_safety_signal or context.customer_escalation or risky(question):
        return 'human_triage', '待人工分诊', '检测到食品安全、赔付、投诉或高风险线索，必须由人工处理。', ''
    missing = []
    if context.damage_type == 'unknown':
        missing.append('破损类型')
    if context.quantity_band == 'unknown':
        missing.append('破损数量')
    if context.evidence_status == 'missing':
        missing.append('证据状态')
    if context.business_fact_status == 'unknown':
        missing.append('业务事实核验')
    if missing:
        return 'needs_evidence', '待补证', '尚缺：' + '、'.join(missing) + '。', '为便于核查，请通过商家原有渠道补充商品和外包装照片，并说明破损数量；核实后为您跟进。'
    return 'ready_for_approval', '待审批', '破损信息和合成业务事实已齐全，可提交主管审批后交仓储物流核查。', '已为您登记破损核查，处理结果将由客服确认后回复。'


def register_collaboration(app, db, provider):
    router = APIRouter(prefix='/api/collaboration')
    # The supported launcher uses one process. Interrupted calls require human review on restart.
    with db.transaction() as tx:
        for row in tx.all('tickets'):
            if row.get('workflow') == 'collaboration' and row['state'] == 'AI审核中':
                row.update(state='待审批', review_reason='上次AI审核中断，重启后转人工，不自动重试。')
                record(tx, row, '重启恢复到人工审核', 'system')

    def human(request, claimed_role, *allowed_roles):
        principal = require_role(request, *allowed_roles)
        if app.state.auth.enabled:
            return principal.role, actor_ref(request, claimed_role)
        require(claimed_role in allowed_roles, 403, '当前本地演示角色无此动作权限。')
        return claimed_role, actor_ref(request, claimed_role)

    @router.get('/state')
    def snapshot(request: Request):
        principal = require_role(request, '客服', '主管', '运营', '供应链', '仓储物流', '财务', '质量')
        with db.transaction() as tx:
            tickets = []
            for row in tx.all('tickets'):
                if row.get('workflow') != 'collaboration':
                    continue
                ok, reason = eligible(tx, row)
                row.update(batch_eligible=ok and row['state'] == '待审批', eligibility_reason=reason)
                if not app.state.auth.enabled or principal.role in ('客服', '主管') or row['department'] == principal.role:
                    tickets.append(row)
            return {'policy': policy(tx), 'tickets': sorted(tickets, key=lambda x: x['updated_at'], reverse=True),
                    'categories': CATEGORIES, 'roles': ['客服', '主管', '运营', '供应链', '仓储物流', '财务', '质量'],
                    'model_configured': bool(os.environ.get('CS_MODEL_API_KEY')),
                    'auth': 'server-verified-login' if app.state.auth.enabled else 'local-role-simulation',
                    'role': principal.role if principal else None}

    @router.put('/policy')
    def set_policy(body: PolicyInput, request: Request):
        _, actor = human(request, body.actor, '主管')
        require(body.mode != 'ai' or bool(os.environ.get('CS_MODEL_API_KEY')), 409, '模型未配置，不能开启 AI 自动审批。')
        with db.transaction() as tx:
            previous = policy(tx)
            require(previous['version'] == body.expected_version, 409, '规则已变化，请刷新。')
            result = {'mode': body.mode, 'version': previous['version'] + 1}
            tx.put('store_meta', 'collaboration_policy', result)
            key = uuid4().hex
            tx.put('audit_events', key, {'id': key, 'action': 'collaboration_policy:' + body.mode,
                   'object_id': 'demo', 'actor': actor, 'at': now()})
            return result

    def automatic(key, submitted_version):
        # Reserve before leaving the transaction; never hold a lock during a model call.
        with db.transaction() as tx:
            row = get_ticket(tx, key)
            if row['state'] != '待审批' or row['version'] != submitted_version:
                return row
            rules = policy(tx)
            ok, reason = eligible(tx, row)
            meta = tx.get('store_meta', 'demo')
            row['policy_version'] = rules['version']
            if rules['mode'] == 'manual' or not ok or meta['paused']:
                row['review_reason'] = '人工模式或全局暂停，等待审核。' if ok else reason
                tx.put('tickets', key, row)
                return row
            if rules['mode'] == 'rules':
                dispatch(row, 'rule-v1')
                record(tx, row, '规则自动通过并派发', 'system:rule', reason)
                return row
            if not os.environ.get('CS_MODEL_API_KEY') or meta['model_calls'] >= int(os.environ.get('CS_MODEL_CALL_LIMIT', '50')):
                row['review_reason'] = '模型未配置或额度上限，回到人工审核。'
                tx.put('tickets', key, row)
                return row
            meta['model_calls'] += 1
            tx.put('store_meta', 'demo', meta)
            row['state'] = 'AI审核中'
            record(tx, row, 'AI审核预约', 'system')
            reserved_version = row['version']
            question = tx.get('conversations', row['conversation_id'])['messages'][-1]['text']
        try:
            decision = provider.review_ticket(question, row['category'])
            approved = decision == {'category': row['category'], 'approve': True, 'needs_human': False}
        except ModelUnavailable:
            approved = False
        with db.transaction() as tx:
            current = get_ticket(tx, key)
            if current['version'] != reserved_version or current['state'] != 'AI审核中':
                return current
            still_ok, _ = eligible(tx, current)
            unchanged = policy(tx) == rules and not tx.get('store_meta', 'demo')['paused']
            if approved and still_ok and unchanged:
                dispatch(current, model_provider() + '+rule-v1')
                record(tx, current, 'AI建议通过且规则复核通过并派发', 'system:ai+rule')
            else:
                current.update(state='待审批', review_reason='AI未通过、调用失败或上下文/规则变化，转人工。')
                record(tx, current, 'AI审核转人工', 'system', current['review_reason'])
            return current

    @router.post('/tickets')
    def create(body: CreateInput, request: Request):
        _, actor = human(request, body.actor, '客服')
        with db.transaction() as tx:
            conv = tx.get('conversations', str(body.conversation_id))
            require(conv is not None, 404, '咨询不存在。')
            require(conv['revision'] == body.expected_revision, 409, '咨询已更新，请刷新。')
            key = f'{conv["id"]}:{conv["revision"]}'
            prior = tx.get('tickets', key)
            if prior:
                require(prior.get('workflow') == 'collaboration', 409, '此消息已有旧版工单，请在原工单处理。')
                require(prior['category'] == body.category, 409, '已有工单分类不同，请通过补充重提修改。')
                return prior
            active = [t for t in tx.all('tickets') if t.get('workflow') == 'collaboration' and t['conversation_id'] == conv['id'] and t['state'] != '已结案']
            require(not active, 409, '此会话已有未结协作工单，请补充重提，不重复建单。')
            row = {'id': key, 'workflow': 'collaboration', 'conversation_id': conv['id'], 'message_revision': conv['revision'],
                   'summary': CATEGORIES[body.category]['label'], 'category': body.category, 'department': None,
                   'state': '待分诊' if body.category == 'unknown' else '待审批', 'version': 0,
                   'resolution': '', 'history': [], 'created_at': now(), 'approval_source': None, 'dispatch_status': 'not_dispatched'}
            record(tx, row, '客服提交工单', actor)
        return automatic(key, row['version'])

    @router.post('/inventory/evaluate-and-ticket')
    def evaluate_and_ticket(body: InventoryTicketInput, request: Request):
        _, actor = human(request, body.actor, '客服')
        with db.transaction() as tx:
            result = evaluate_inventory(tx, body)
            evaluation_id = uuid4().hex
            tx.put('inventory_evaluations', evaluation_id, {'id': evaluation_id, 'conversation_id': str(body.conversation_id), 'message_revision': body.expected_revision, 'sku': body.sku, 'target_grams': body.target_grams, 'result': result, 'created_at': now()})
            if result.get('status') not in ('out_of_stock', 'unknown'):
                return {'evaluation_id': evaluation_id, 'evaluation': result, 'ticket': None}
            conv = tx.get('conversations', str(body.conversation_id))
            require(conv is not None and conv['revision'] == body.expected_revision, 409, '咨询已更新，请刷新。')
            key = f'{conv["id"]}:{conv["revision"]}'
            prior = tx.get('tickets', key)
            if prior:
                require(prior.get('workflow') == 'collaboration', 409, '此消息已有旧版工单，请在原工单处理。')
                return {'evaluation_id': evaluation_id, 'evaluation': result, 'ticket': prior}
            active = [t for t in tx.all('tickets') if t.get('workflow') == 'collaboration' and t['conversation_id'] == conv['id'] and t['state'] != '已结案']
            require(not active, 409, '此会话已有未结协作工单，请补充重提，不重复建单。')
            row = {'id': key, 'workflow': 'collaboration', 'conversation_id': conv['id'], 'message_revision': conv['revision'], 'summary': '库存事实核验与替代规格评估', 'category': 'stock', 'department': None, 'state': '待审批', 'version': 0, 'resolution': '', 'history': [], 'created_at': now(), 'approval_source': None, 'dispatch_status': 'not_dispatched', 'inventory_evaluation_id': evaluation_id, 'inventory_evidence': result}
            record(tx, row, '库存事实核验生成协作工单', actor)
        return {'evaluation_id': evaluation_id, 'evaluation': result, 'ticket': automatic(key, row['version'])}

    @router.get('/damage/state')
    def damage_state(request: Request):
        principal = require_role(request, '客服', '主管', '运营', '供应链', '仓储物流', '财务', '质量')
        with db.transaction() as tx:
            rows = tx.all('damage_cases')
            if app.state.auth.enabled and principal.role not in ('客服', '主管'):
                rows = [row for row in rows if row.get('department') == principal.role]
            return {'cases': sorted(rows, key=lambda row: row['created_at'], reverse=True)[:50]}

    @router.post('/damage/evaluate-and-ticket')
    def evaluate_damage_and_ticket(body: DamageCaseInput, request: Request):
        _, actor = human(request, body.actor, '客服')
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()
        receipt_key = 'damage-case:' + str(body.request_id)
        with db.transaction() as tx:
            prior = tx.get('received_events', receipt_key)
            if prior:
                require(prior['fingerprint'] == fingerprint, 409, '破损核验请求标识已用于不同内容。')
                return prior['result']
            conv = tx.get('conversations', str(body.conversation_id))
            require(conv is not None, 404, '咨询不存在。')
            require(conv['revision'] == body.expected_revision, 409, '咨询已更新，请刷新后重新核验。')
            existing = next((row for row in tx.all('damage_cases') if row['conversation_id'] == conv['id'] and row['message_revision'] == conv['revision']), None)
            require(existing is None, 409, '当前咨询版本已有破损核验；补充新消息后再重新判断。')
            route, state, reason, reply_draft = damage_decision(conv['messages'][-1]['text'], body.context)
            case = {'id': uuid4().hex, 'conversation_id': conv['id'], 'message_revision': conv['revision'],
                    'context': body.context.model_dump(), 'route': route, 'state': state, 'reason': reason,
                    'reply_draft': reply_draft, 'department': '仓储物流' if route == 'ready_for_approval' else None,
                    'ticket_id': None, 'created_at': now(), 'actor': actor}
            tx.put('damage_cases', case['id'], case)
            ticket = None
            if route == 'ready_for_approval':
                key = f'{conv["id"]}:{conv["revision"]}'
                prior_ticket = tx.get('tickets', key)
                require(prior_ticket is None, 409, '此咨询已有工单，请在原工单处理。')
                active = [row for row in tx.all('tickets') if row.get('workflow') == 'collaboration' and row['conversation_id'] == conv['id'] and row['state'] != '已结案']
                require(not active, 409, '此会话已有未结协作工单，请在原工单处理。')
                ticket = {'id': key, 'workflow': 'collaboration', 'conversation_id': conv['id'], 'message_revision': conv['revision'],
                          'summary': CATEGORIES['damage']['label'], 'category': 'damage', 'department': None,
                          'state': '待审批', 'version': 0, 'resolution': '', 'history': [], 'created_at': now(),
                          'approval_source': None, 'dispatch_status': 'not_dispatched', 'damage_case_id': case['id'],
                          'damage_context': case['context']}
                record(tx, ticket, '破损上下文核验生成协作工单', actor, reason)
                case['ticket_id'] = ticket['id']
                tx.put('damage_cases', case['id'], case)
            audit = uuid4().hex
            tx.put('audit_events', audit, {'id': audit, 'action': 'damage_case:' + route, 'object_id': case['id'], 'actor': actor, 'at': now()})
            result = {'case': case, 'ticket': ticket}
            tx.put('received_events', receipt_key, {'fingerprint': fingerprint, 'result': result})
        return result

    def approve(tx, row, actor, batch=False):
        require(row['state'] == '待审批', 409, '该工单不在待审批状态。')
        conv = tx.get('conversations', row['conversation_id'])
        require(conv['revision'] == row['message_revision'], 409, '咨询已更新，请补充重提。')
        if batch:
            ok, reason = eligible(tx, row)
            require(ok, 422, reason)
        require(row['category'] != 'unknown', 422, '请先明确处理部门。')
        dispatch(row, 'human-batch' if batch else 'human-single')
        record(tx, row, '批量人工通过并派发' if batch else '人工通过并派发', actor, '仅批准核查或评估，不执行资金或采购操作。')

    @router.post('/batch-approve')
    def batch_approve(body: BatchInput, request: Request):
        _, actor = human(request, body.actor, '主管')
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()
        key = 'collaboration-batch:' + str(body.request_id)
        # All successful row changes and the result receipt commit together. Invalid rows are skipped.
        with db.transaction() as tx:
            prior = tx.get('received_events', key)
            if prior:
                require(prior['fingerprint'] == fingerprint, 409, '批次标识已用于不同内容。')
                return prior['result']
            results = []
            for item in body.items:
                try:
                    row = get_ticket(tx, item.id)
                    require(row['version'] == item.expected_version, 409, '工单版本已变化，请重新审核。')
                    approve(tx, row, actor, batch=True)
                    results.append({'id': item.id, 'ok': True, 'state': row['state'], 'version': row['version']})
                except HTTPException as error:
                    results.append({'id': item.id, 'ok': False, 'reason': error.detail})
            result = {'request_id': str(body.request_id), 'results': results}
            tx.put('received_events', key, {'fingerprint': fingerprint, 'result': result})
            return result

    @router.post('/tickets/{ticket_id}/actions')
    def action(ticket_id: str, body: ActionInput, request: Request):
        note = clean(body.note) if body.note else ''
        with db.transaction() as tx:
            row = get_ticket(tx, ticket_id)
            require(row['version'] == body.expected_version, 409, '工单已变化，请刷新。')
            if body.action == 'approve':
                _, actor = human(request, body.actor, '主管')
                approve(tx, row, actor)
                return row
            if body.action == 'reject':
                _, actor = human(request, body.actor, '主管')
                require(row['state'] == '待审批' and note, 422, '需待审批工单及驳回原因。')
                row.update(state='待补充', department=None)
            elif body.action == 'resubmit':
                _, actor = human(request, body.actor, '客服')
                conv = tx.get('conversations', row['conversation_id'])
                stale = conv['revision'] != row['message_revision']
                require(row['state'] in ('待补充', '待分诊') or (stale and row['state'] != '已结案'), 409, '当前工单不允许重提。')
                require(note and body.category is not None, 422, '需要补充说明及分类。')
                row.update(category=body.category, summary=CATEGORIES[body.category]['label'], state='待分诊' if body.category == 'unknown' else '待审批',
                           message_revision=conv['revision'], department=None, approval_source=None, dispatch_status='not_dispatched', resolution='', supplement=note)
            else:
                conv = tx.get('conversations', row['conversation_id'])
                require(conv['revision'] == row['message_revision'], 409, '咨询更新使审批失效，请客服补充重提。')
                transitions = {'accept': ('待接单', '处理中'), 'feedback': ('处理中', '待客服确认'),
                               'return': ('待客服确认', '处理中'), 'close': ('待客服确认', '已结案')}
                before, after = transitions[body.action]
                expected_role = '客服' if body.action in ('return', 'close') else row['department']
                _, actor = human(request, body.actor, expected_role)
                require(row['state'] == before, 409, '不允许该状态变化。')
                require(body.action == 'accept' or note, 422, '请填写依据、结果或退回原因。')
                row['state'] = after
                if body.action == 'feedback':
                    row['resolution'] = note
            record(tx, row, body.action, actor, note)
            # Resubmissions deliberately return to manual review; old approvals never carry over.
            return row

    app.include_router(router)
