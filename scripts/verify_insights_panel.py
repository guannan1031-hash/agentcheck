"""验证面板数据反哺区块渲染。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 1200})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8878/quality-panel.html")
    pg.wait_for_timeout(2500)
    t = pg.evaluate("document.getElementById('insightsZone').innerText")
    print("== 数据反哺区 ==")
    print(t[:400].replace("\n", " | "))
    print("== errors ==", errs if errs else "无")
    pg.screenshot(path="deliverables/shots/panel-insights.png")
    b.close()
