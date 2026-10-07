"""将质检面板拆分为 HTML/CSS/JS 三件套，满足后端 CSP(script-src 'self') 约束。"""
import re
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
HTML = DIST / "quality-panel.html"

text = HTML.read_text(encoding="utf-8")

style = re.search(r"<style>([\s\S]*?)</style>", text).group(1)
script = re.search(r"<script>([\s\S]*?)</script>", text).group(1)

(DIST / "quality-panel.css").write_text(style, encoding="utf-8")
(DIST / "quality-panel.js").write_text(script, encoding="utf-8")

text = text.replace("<style>" + style + "</style>", '<link rel="stylesheet" href="/quality-panel.css">')
text = text.replace("<script>" + script + "</script>", '<script src="/quality-panel.js"></script>')
HTML.write_text(text, encoding="utf-8")

print("已拆分:", (DIST / "quality-panel.css").stat().st_size, "bytes css,", (DIST / "quality-panel.js").stat().st_size, "bytes js")
print("HTML 引用:", 'quality-panel.css' in text and 'quality-panel.js' in text)
