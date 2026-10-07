"""调试质检面板：捕获 console/pageerror，检查 JS 状态。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    msgs = []
    pg.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
    pg.on("pageerror", lambda e: msgs.append(f"[pageerror] {e}"))
    pg.on("requestfailed", lambda r: msgs.append(f"[reqfail] {r.url} {r.failure}"))
    pg.goto("http://127.0.0.1:8878/quality-panel.html")
    pg.wait_for_timeout(2500)
    print("== 控制台/错误 ==")
    for m in msgs[:30]:
        print(m)
    print("== JS 状态 ==")
    print("init typeof:", pg.evaluate("typeof init"))
    print("badge:", pg.evaluate("document.getElementById('engineBadge').textContent"))
    print("pkg options:", pg.evaluate("document.getElementById('package').options.length"))
    b.close()
