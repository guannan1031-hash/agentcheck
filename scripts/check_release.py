"""Check the V1.7 handoff archive for local data, paths and likely secrets."""
import re
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "output/releases/ecommerce-customer-service-agent-v1.7.0-source.zip"
REQUIRED = {
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "scripts/setup_windows.ps1",
    "scripts/start_windows.ps1",
    "scripts/test_windows.ps1",
    "frontend/dist/index.html",
    "configs/scenarios-v1.4.json",
    "Dockerfile",
    "compose.yaml",
    "docs/DEPLOYMENT_READINESS.md",
}
FORBIDDEN_NAME_PARTS = (
    "/state/local/",
    "/output/backups/",
    "/input/",
    "/.venv/",
    "/node_modules/",
    "售后场景流程最终版.xlsx",
)
TEXT_SUFFIXES = {".py", ".js", ".jsx", ".json", ".md", ".css", ".html", ".ps1", ".sh", ".txt", ".yaml", ".conf", ".template"}
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{20,}"),
    re.compile(r"CS_MODEL_API_KEY\s*=\s*[^\s$%<{][^\s]{7,}"),
    re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^\s:@/$]+:(?!\$\{)[^\s@/]+@", re.I),
)
LOCAL_PATH = re.compile(r"/(?:Users|home)/[^/\s]+/")


def main():
    if not ARCHIVE.is_file():
        raise SystemExit("尚未找到发布包，请先运行 python scripts/package_release.py。")
    findings = []
    with ZipFile(ARCHIVE) as bundle:
        if bundle.testzip() is not None:
            findings.append("ZIP 完整性失败")
        names = bundle.namelist()
        relative_names = {name.split("/", 1)[1] for name in names if "/" in name}
        missing = REQUIRED - relative_names
        if missing:
            findings.append("缺少必需文件：" + "、".join(sorted(missing)))
        for name in names:
            if any(part in name for part in FORBIDDEN_NAME_PARTS) or name.endswith((".env", ".db", ".sqlite")):
                findings.append("禁止文件：" + name)
            if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            content = bundle.read(name).decode("utf-8", errors="ignore")
            if LOCAL_PATH.search(content):
                findings.append("包含本机绝对路径：" + name)
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                findings.append("包含疑似真实凭据：" + name)
    if findings:
        raise SystemExit("发布检查未通过：\n- " + "\n- ".join(findings[:20]))
    print(f"发布检查通过：{len(names)} 个 ZIP 条目；未发现本机数据、绝对用户路径或疑似真实凭据。")
    print("许可证和场景公开权仍需仓库所有者在公开发布前确认。")


if __name__ == "__main__":
    main()
