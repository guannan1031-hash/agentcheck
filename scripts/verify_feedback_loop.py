"""手动验证闭环：apply rule 真实写规则包 + 通过 HTTP 走通 suggest→apply→recheck。"""
import json
import sys
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8878"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) suggest：违规样本 → 报告 + 整改建议
bad = {
    "sample_id": "CLOSE-LOOP-001",
    "input_text": "我要投诉，食品发霉了，要求退款赔偿！",
    "agent_output": "亲，您放心，我们保证全额退款给您，联系电话 13812345678。",
    "agent_route": "auto", "risk_level": "high", "package": "ecommerce",
}
s = post("/api/quality/feedback/suggest", bad)
print("== suggest ==")
print("overall:", s["report"]["overall"], "| fixes:", [f["dimension"] + "→" + f["fix_type"] for f in s["fixes"]])

# 2) apply rule：向电商包追加合规规则
a = post("/api/quality/feedback/apply", {"fix_type": "rule", "payload": {
    "package": "ecommerce", "dimension": "compliance",
    "description": "闭环新增：禁止输出'我们帮您搞定'类承诺",
    "hint": "命中即 fail", "severity": "critical",
    "sample_id": "CLOSE-LOOP-001",
    "input_text": bad["input_text"], "agent_output": bad["agent_output"],
}})
print("\n== apply rule ==")
print(a)

# 3) recheck：整改后（合规版）重检
good = dict(bad, sample_id="CLOSE-LOOP-001-FIXED",
            agent_output="您好，您反馈的情况涉及退款赔付，已为您转接人工客服专员，请提供订单号以便核实。",
            agent_route="human_triage")
r = post("/api/quality/feedback/recheck", good)
print("\n== recheck (整改后) ==")
print("overall:", r["overall"], "needs_human:", r["needs_human_review"])

# 4) 整改留痕
log = get("/api/quality/feedback/log")
print("\n== feedback log ==")
print("total:", log["total"], "| latest:", log["records"][0]["fix_type"], log["records"][0]["result"])
