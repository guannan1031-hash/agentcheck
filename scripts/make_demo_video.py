"""生成参赛演示视频：Playwright 驱动质检工作台 + PIL 文字卡/字幕 + imageio-ffmpeg 合成 MP4。

流程：开场卡 → 架构卡 → 违规样本质检(fail) → 合规样本质检(pass) → 金融规则包切换(fail)
     → 批量全量质检(统计) → 结尾卡。约 90 秒，1280x720，25fps。
"""
from __future__ import annotations

import sys
from pathlib import Path

import imageio.v2 as iio
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deliverables/demo.mp4"
TMP = ROOT / "deliverables/frames"
W, H, FPS = 1280, 720, 25

FONT_TITLE = "C:/Windows/Fonts/msyhbd.ttc"
FONT_BODY = "C:/Windows/Fonts/msyh.ttc"
BG = (8, 12, 20)
ACCENT = (79, 140, 255)
TEXT = (232, 237, 243)
MUTED = (139, 152, 169)

frames: list[np.ndarray] = []


def make_text_card(title: str, sub: str, accent: tuple = ACCENT) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title = ImageFont.truetype(FONT_TITLE, 54)
    f_sub = ImageFont.truetype(FONT_BODY, 26)
    tw = d.textlength(title, font=f_title)
    d.text(((W - tw) / 2, H / 2 - 90), title, font=f_title, fill=accent)
    lines = sub.split("\n")
    line_h = 38
    start_y = H / 2 + 20 - (len(lines) - 1) * line_h / 2
    for i, line in enumerate(lines):
        sw = d.textlength(line, font=f_sub)
        d.text(((W - sw) / 2, start_y + i * line_h), line, font=f_sub, fill=MUTED)
    return img


def add_subtitle(img: Image.Image, text: str) -> Image.Image:
    """底部字幕条。"""
    img = img.convert("RGB").copy()
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT_BODY, 22)
    bar_h = 64
    bar = Image.new("RGBA", (W, bar_h), (0, 0, 0, 150))
    img.paste(bar, (0, H - bar_h), bar)
    tw = d.textlength(text, font=f)
    d.text(((W - tw) / 2, H - bar_h + 18), text, font=f, fill=(255, 255, 255))
    return img


def hold(img: Image.Image, seconds: float, subtitle: str = "") -> None:
    img = add_subtitle(img, subtitle) if subtitle else img.convert("RGB")
    arr = np.asarray(img)
    for _ in range(int(seconds * FPS)):
        frames.append(arr)


def shot(page, seconds: float, subtitle: str) -> None:
    """截面板当前态（1280x900 → resize 720 高）+ 字幕 + 停留。"""
    path = TMP / f"f{len(frames):04d}.png"
    page.screenshot(path=str(path))
    img = Image.open(path).convert("RGB").resize((W, H), Image.LANCZOS)
    hold(img, seconds, subtitle)


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    for f in TMP.glob("*.png"):
        f.unlink()

    hold(make_text_card("AgentCheck", "Agent 输出质检工具 · 跨平台 Agent 输出的统一质量门禁"), 4.0)
    hold(make_text_card("毫秒级全量质检", "规则层 0.4ms 拦截 → Jev 语义快判 → LLM 深析兜底 → 置信度升级人工"), 4.0)

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.goto("http://127.0.0.1:8878/quality-panel.html")
        pg.wait_for_timeout(1500)

        hold(make_text_card("单条质检 · 违规检出", "客户投诉食品发霉要求退款，Agent 却自动承诺全额退款并留电话"), 2.5)
        pg.click("button[data-preset='bad']")
        pg.wait_for_timeout(400)
        pg.click("#run")
        pg.wait_for_timeout(1500)
        shot(pg, 8.0, "4 项规则全红：路由未转人工 / 路由不合法 / 泄露手机号 / 承诺全额退款 —— 0.7ms 判定违规并升级人工复核")

        # ---- 闭环整改：检出 → 建议 → 整改 → 重检 ----
        hold(make_text_card("质检反馈闭环", "检出违规 → 生成整改建议 → 应用整改 → 重检验证 fail→pass → 回归防复发"), 3.0)
        pg.click("#suggestBtn")
        pg.wait_for_timeout(1400)
        shot(pg, 6.0, "整改建议按失败维度生成：路由策略增强 / 隐私规则增强 / 合规规则增强，对应 rule/knowledge/prompt 三种整改类型")

        pg.click("button[data-dim='compliance']")
        pg.wait_for_timeout(1400)
        shot(pg, 6.0, "应用整改：真实写入规则包（新增 R-COMP-010、规则总数 10、版本 0.3），带 pattern 立即生效，整改留痕并登记回归用例")

        pg.click("#recheckBtn2")
        pg.wait_for_timeout(1400)
        shot(pg, 7.0, "重检验证：整改前 FAIL → 整改后 PASS（得分 1.00）—— Agent 持续进化的闭环真实落地")

        hold(make_text_card("同一样本 · 正确路由", "Agent 将退款赔付场景转接人工客服，输出合规"), 2.0)
        pg.click("button[data-preset='good']")
        pg.wait_for_timeout(400)
        pg.click("#run")
        pg.wait_for_timeout(1500)
        shot(pg, 6.0, "合规样本：全通道通过，不升级人工 —— 质检只拦违规，不误伤正常业务")

        hold(make_text_card("可插拔规则包 · 汽车金融", "引擎零改动，仅切换规则定义：催收合规 / 征信红线 / 减免承诺"), 2.5)
        pg.click("button[data-preset='finance']")
        pg.wait_for_timeout(400)
        pg.click("#run")
        pg.wait_for_timeout(1500)
        shot(pg, 8.0, "金融违规样本：减免利息承诺 + 不上征信 + 凌晨联系 —— 金融规则包全部拦截")

        hold(make_text_card("批量全量质检", "从抽检升级为全量：一次质检 40 条，全部正确分类"), 2.0)
        pg.click("#batch")
        pg.wait_for_timeout(1800)
        shot(pg, 8.0, "批量统计：40 条样本 100% 正确分类，平均 0.4ms/条，违规全部检出并升级人工 —— 全量质检成为可能")

        b.close()

    hold(make_text_card("开源 · 可插拔 · 可解释", "MIT 许可 · 218 场景基准 · 110+ 测试 · 规则包可跨行业复用"), 4.0)
    hold(make_text_card("谢谢", "AgentCheck · 2026 上海开源软件应用创新大赛\n作者：guannan · AgentCheck 团队"), 3.5)

    # 合成 MP4
    writer = iio.get_writer(str(OUT), fps=FPS, codec="libx264", quality=8,
                            macro_block_size=16,
                            output_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    for arr in frames:
        writer.append_data(arr)
    writer.close()
    print(f"视频已生成: {OUT} ({OUT.stat().st_size/1024/1024:.1f} MB, {len(frames)/FPS:.0f}s @ {FPS}fps)")


if __name__ == "__main__":
    main()
