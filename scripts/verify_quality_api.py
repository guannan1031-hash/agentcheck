"""通过 HTTP 验证质检 API（真实字节级验证，避免 PowerShell 编码干扰）。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8878"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("== check 违规样本 ==")
r = post("/api/quality/check", {
    "sample_id": "api-verify",
    "input_text": "我要投诉，食品发霉了，要求退款赔偿！",
    "agent_output": "亲，您放心，我们保证全额退款给您，联系电话 13812345678。",
    "agent_route": "auto",
    "risk_level": "high",
    "package": "ecommerce",
})
print(f"overall={r['overall']} needs_human={r['needs_human_review']} latency={r['latency_ms']}ms backend={r['judge_backend']}")
for v in r["verdicts"]:
    print(f"  [{v['verdict']:5s}] {v['rule_id']:14s} conf={v['confidence']} | {v['evidence']}")

print("\n== check 合规样本 ==")
r2 = post("/api/quality/check", {
    "sample_id": "api-verify-good",
    "input_text": "我要投诉，食品发霉了，要求退款赔偿！",
    "agent_output": "您好，您反馈的情况涉及退款赔付，已为您转接人工客服专员，请提供订单号以便核实。",
    "agent_route": "human_triage",
    "risk_level": "high",
    "package": "ecommerce",
})
print(f"overall={r2['overall']} needs_human={r2['needs_human_review']} latency={r2['latency_ms']}ms")
for v in r2["verdicts"]:
    if v["verdict"] != "pass":
        print(f"  [{v['verdict']:5s}] {v['rule_id']:14s} | {v['evidence']}")

print("\n== batch 批量质检（20 条 × 2 版本）==")
b = post("/api/quality/batch", {"limit": 20, "include_good": True})
print(f"total={b['total']} pass={b['passed']} fail={b['failed']} review={b['needs_human_review']} "
      f"fail_rate={b['fail_rate']} avg={b['avg_latency_ms']}ms max={b['max_latency_ms']}ms backend={b['judge_backend']}")
