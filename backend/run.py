"""Container entry point. Runtime accounts are read from a mounted secret file."""
import json
import os
from pathlib import Path

import uvicorn

from .app import create_app


def load_accounts():
    filename = os.environ.get("CS_AUTH_ACCOUNTS_FILE", "").strip()
    if not filename:
        return None
    try:
        content = Path(filename).read_text(encoding="utf-8")
        accounts = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("无法读取运行时账号密钥文件。") from error
    if not isinstance(accounts, dict):
        raise SystemExit("运行时账号密钥文件必须是 JSON 对象。")
    return accounts


if __name__ == "__main__":
    uvicorn.run(create_app(auth_accounts=load_accounts()), host="0.0.0.0", port=8878, workers=1, access_log=False)
