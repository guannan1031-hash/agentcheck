"""调试：完整模拟点击流程，检查报告是否渲染。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    msgs = []
    pg.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
    pg.on("pageerror", lambda e: msgs.append(f"[pageerror] {e}"))
    pg.on("requestfailed", lambda r: msgs.append(f"[reqfail] {r.url} {r.failure}"))
    pg.goto("http://127.0.0.1:8878/quality-panel.html")
    pg.wait_for_timeout(1500)

    # 点击违规样本预设
    pg.click("button:has-text('违规样本')")
    pg.wait_for_timeout(300)
    print("input_text:", pg.evaluate("document.getElementById('inputText').value")[:20])
    print("agent_output:", pg.evaluate("document.getElementById('agentOutput').value")[:20])

    # 点击开始质检
    pg.click("#run")
    pg.wait_for_timeout(1500)
    print("metric:", pg.evaluate("document.getElementById('metric').textContent"))
    result_html = pg.evaluate("document.getElementById('result').innerHTML")
    print("result 长度:", len(result_html), "| 前100:", result_html[:100].replace("\n", " "))

    print("== 错误 ==")
    for m in msgs[-15:]:
        print(m)
    b.close()
