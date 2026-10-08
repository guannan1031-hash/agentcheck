"""生成参赛作品介绍 PDF（HTML → PDF，使用系统 Edge）并截图质检工作台状态。"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
INTRO_HTML = ROOT / "deliverables/作品介绍-Agent质检工具.html"
INTRO_PDF = ROOT / "deliverables/作品介绍-Agent质检工具-v1.2.pdf"
SHOTS = ROOT / "deliverables/shots"


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # 1) 介绍文档 → PDF
        page = browser.new_page(viewport={"width": 1280, "height": 1600})
        page.goto(INTRO_HTML.as_uri())
        page.wait_for_timeout(800)
        page.pdf(
            path=str(INTRO_PDF),
            format="A4",
            print_background=True,
            margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
        )
        print("PDF 已生成:", INTRO_PDF, INTRO_PDF.stat().st_size, "bytes")

        # 2) 质检工作台截图（初始态 → 违规质检 → 批量）
        page2 = browser.new_page(viewport={"width": 1280, "height": 900})
        page2.goto("http://127.0.0.1:8878/quality-panel.html")
        page2.wait_for_timeout(1500)
        page2.screenshot(path=str(SHOTS / "panel-init.png"))
        print("截图1: panel-init")

        page2.click("button:has-text('违规样本')")
        page2.wait_for_timeout(300)
        page2.click("#run")
        page2.wait_for_timeout(1200)
        page2.screenshot(path=str(SHOTS / "panel-fail.png"))
        print("截图2: panel-fail")

        page2.click("#batch")
        page2.wait_for_timeout(1500)
        page2.screenshot(path=str(SHOTS / "panel-batch.png"))
        print("截图3: panel-batch")

        # 金融规则包切换
        page2.click("button:has-text('金融违规样本')")
        page2.wait_for_timeout(300)
        page2.click("#run")
        page2.wait_for_timeout(1200)
        page2.screenshot(path=str(SHOTS / "panel-finance.png"))
        print("截图4: panel-finance")

        browser.close()
    print("全部完成")


if __name__ == "__main__":
    main()
