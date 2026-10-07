"""Build the portable V1.7 source handoff without local data or credentials."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/releases"
ARCHIVE_NAME = "ecommerce-customer-service-agent-v1.7.0-source.zip"
ARCHIVE = OUT / ARCHIVE_NAME
PACKAGE_ROOT = "ecommerce-customer-service-agent"

DIRECTORIES = (
    "backend",
    "configs",
    "demo-data",
    "frontend/src",
    "frontend/dist",
    "licenses",
    "scripts",
    "tests",
    "deploy",
)
DOCUMENTS = (
    "docs/PRODUCT_SCOPE.md",
    "docs/SCENARIO_REGISTRY.md",
    "docs/SAFETY_AND_PRIVACY.md",
    "docs/FULL_SCENARIO_ARCHITECTURE_V1_4.md",
    "docs/FULL_SCENARIO_P0_IMPLEMENTATION_V1_4.md",
    "docs/MULTI_DEPARTMENT_CASE_P1_1_V1_5.md",
    "docs/LOW_RISK_WORKFLOW_PACKS_V1_6.md",
    "docs/NOTIFICATION_CONNECTOR_RUNTIME_V1_7.md",
    "docs/LOCAL_DEVELOPMENT_V0_6.md",
    "docs/WINDOWS_HANDOFF_AND_GITHUB_RELEASE.md",
    "docs/GITHUB_PUBLISH_CHECKLIST.md",
    "docs/LICENSE_DECISION.md",
    "docs/LOCAL_LOGIN_V1_0.md",
    "docs/ANONYMOUS_PRODUCT_FAQ.md",
    "docs/PILOT_OPERATIONS_REPORT.md",
    "docs/SINGLE_STORE_PILOT_PLAYBOOK.md",
    "docs/TAOBAO_TMALL_OFFICIAL_CONNECTOR.md",
    "docs/ENTERPRISE_TRIAL_ARCHITECTURE.md",
    "docs/DEPLOYMENT_READINESS.md",
    "docs/CS_COLLEAGUE_45_SECOND_TEST_INVITE.md",
)
ROOT_FILES = (
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".gitignore",
    "requirements.lock.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.js",
    "Dockerfile",
    ".dockerignore",
    "compose.yaml",
    "output/playwright/product-overview-v1.5.png",
    "output/playwright/workflow-pack-v1.6.png",
    "output/playwright/notification-connector-v1.7.png",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".venv", "node_modules", ".DS_Store"}


def collect_files():
    files = []
    for name in DIRECTORIES:
        base = ROOT / name
        files.extend(path for path in base.rglob("*") if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts))
    files.extend(ROOT / name for name in DOCUMENTS + ROOT_FILES)
    missing = [str(path.relative_to(ROOT)) for path in files if not path.is_file()]
    if missing:
        raise SystemExit("发布文件缺失：" + "、".join(missing))
    return sorted(set(files))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = collect_files()
    manifest = []
    with ZipFile(ARCHIVE, "w", ZIP_DEFLATED) as bundle:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            bundle.writestr(f"{PACKAGE_ROOT}/{relative}", content)
            manifest.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        bundle.writestr(
            f"{PACKAGE_ROOT}/SOURCE_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
        )

    with ZipFile(ARCHIVE) as bundle:
        if bundle.testzip() is not None:
            raise SystemExit("发布包完整性检查失败。")
        forbidden = ("/state/local/", "/output/backups/", "/.venv/", "/node_modules/", "/input/")
        bad = [name for name in bundle.namelist() if any(value in name for value in forbidden) or name.endswith((".env", ".db", ".sqlite"))]
        if bad:
            raise SystemExit("发布包包含禁止文件：" + "、".join(bad[:5]))

    checksum = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    (OUT / ARCHIVE_NAME.replace(".zip", ".sha256")).write_text(f"{checksum}  {ARCHIVE_NAME}\n")
    print(f"打包完成：{ARCHIVE.name}，{len(manifest)} 个文件，{ARCHIVE.stat().st_size} 字节。")


if __name__ == "__main__":
    main()
