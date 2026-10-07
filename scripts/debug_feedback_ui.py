"""验证面板闭环 UI：违规质检 → 整改建议 → 应用整改 → 重检。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 1000})
    msgs = []
    pg.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
    pg.on("pageerror", lambda e: msgs.append(f"[pageerror] {e}"))
    pg.goto("http://127.0.0.1:8878/quality-panel.html")
    pg.wait_for_timeout(1500)

    pg.click("button[data-preset='bad']")
    pg.wait_for_timeout(300)
    pg.click("#run")
    pg.wait_for_timeout(1200)
    print("报告:", pg.evaluate("document.getElementById('result').querySelector('.overall .tag')?.textContent"))

    # 闭环：生成整改建议
    pg.click("#suggestBtn")
    pg.wait_for_timeout(1200)
    zone = pg.evaluate("document.getElementById('feedbackZone').innerText")
    print("== 整改建议区 ==")
    print(zone[:300].replace("\n", " | "))

    # 应用整改（compliance 规则）
    pg.click("button[data-dim='compliance']")
    pg.wait_for_timeout(1200)
    zone2 = pg.evaluate("document.getElementById('feedbackZone').innerText")
    print("== 应用整改后 ==")
    print(zone2[:300].replace("\n", " | "))

    # 重检
    pg.click("#recheckBtn2")
    pg.wait_for_timeout(1200)
    zone3 = pg.evaluate("document.getElementById('feedbackZone').innerText")
    print("== 重检后 ==")
    print(zone3[:300].replace("\n", " | "))

    print("== 错误 ==")
    for m in msgs:
        if "404" not in m:
            print(m)
    b.close()
