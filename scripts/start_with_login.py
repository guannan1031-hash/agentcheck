"""Start local login mode without writing account passwords to disk."""
import argparse
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from backend.app import create_app
from backend.auth import ROLES, TENANT_REF


parser = argparse.ArgumentParser(description="本地登录模式；账号口令仅保存在本次进程内存。")
parser.add_argument("--account", action="append", required=True, metavar="账号:角色[:租户]",
                    help="可重复，例如 --account service:客服:alpha-shop；省略租户时使用 local-demo")
args = parser.parse_args()
accounts = {}
for value in args.account:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise SystemExit("账号格式无效、重复，或角色不支持。")
    user_ref, role = parts[:2]
    tenant_id = parts[2] if len(parts) == 3 else "local-demo"
    if not user_ref or role not in ROLES or not TENANT_REF.fullmatch(tenant_id) or user_ref in accounts:
        raise SystemExit("账号格式无效、重复，或角色不支持。")
    password = getpass(f"为 {user_ref}（{role}，租户 {tenant_id}）设置本次启动口令（至少12位）：")
    if len(password) < 12:
        raise SystemExit("口令至少12位；未启动服务。")
    accounts[user_ref] = {"role": role, "tenant_id": tenant_id, "password": password}

app = create_app(auth_accounts=accounts)
uvicorn.run(app, host="127.0.0.1", port=8878, workers=1, access_log=False)
