"""Runtime-only local login for a single-store development deployment."""
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request


COOKIE_NAME = "cs_local_session"
CSRF_HEADER = "x-csrf-token"
ROLES = {"客服", "主管", "运营", "供应链", "仓储物流", "财务", "质量"}
USER_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
TENANT_REF = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


@dataclass(frozen=True)
class Principal:
    user_ref: str
    role: str
    tenant_id: str


class LocalAuth:
    """Passwords and sessions live only in this process and are lost on restart."""

    def __init__(self, accounts=None, session_seconds=8 * 60 * 60):
        self.enabled = accounts is not None
        self.session_seconds = session_seconds
        self._accounts = {}
        self._sessions = {}
        if not self.enabled:
            return
        if not isinstance(accounts, dict) or not accounts:
            raise ValueError("登录模式至少需要一个运行时账号。")
        for user_ref, account in accounts.items():
            if not isinstance(user_ref, str) or not USER_REF.fullmatch(user_ref):
                raise ValueError("账号标识只能使用字母、数字、下划线或连字符，且以字母开头。")
            tenant_id = account.get("tenant_id", "local-demo") if isinstance(account, dict) else None
            if not isinstance(account, dict) or account.get("role") not in ROLES or not isinstance(account.get("password"), str) or len(account["password"]) < 12 or not isinstance(tenant_id, str) or not TENANT_REF.fullmatch(tenant_id):
                raise ValueError("账号需包含有效角色、租户标识和至少12位的运行时口令。")
            salt = secrets.token_bytes(16)
            self._accounts[user_ref] = {"role": account["role"], "tenant_id": tenant_id, "salt": salt,
                                        "digest": self._hash(account["password"], salt)}

    @staticmethod
    def _hash(password, salt):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def login(self, user_ref, password):
        account = self._accounts.get(user_ref)
        supplied = self._hash(password, account["salt"]) if account else self._hash(password, b"\0" * 16)
        if not account or not hmac.compare_digest(supplied, account["digest"]):
            return None
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        self._sessions[self._token_hash(token)] = {"user_ref": user_ref, "role": account["role"], "tenant_id": account["tenant_id"], "csrf": csrf,
                                                   "expires_at": time.monotonic() + self.session_seconds}
        return token, csrf, Principal(user_ref, account["role"], account["tenant_id"])

    def principal(self, request: Request):
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        session = self._sessions.get(self._token_hash(token))
        if not session or session["expires_at"] <= time.monotonic():
            if session:
                self._sessions.pop(self._token_hash(token), None)
            return None
        return Principal(session["user_ref"], session["role"], session["tenant_id"])

    def csrf_valid(self, request: Request):
        token = request.cookies.get(COOKIE_NAME)
        session = self._sessions.get(self._token_hash(token)) if token else None
        return bool(session and session["expires_at"] > time.monotonic() and hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), session["csrf"]))

    def csrf(self, request: Request):
        token = request.cookies.get(COOKIE_NAME)
        session = self._sessions.get(self._token_hash(token)) if token else None
        return session["csrf"] if session else None

    def logout(self, request: Request):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            self._sessions.pop(self._token_hash(token), None)


def require_role(request: Request, *roles):
    auth = request.app.state.auth
    if not auth.enabled:
        return None
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise HTTPException(401, "请先登录。")
    if principal.role not in roles:
        raise HTTPException(403, "当前账号无此操作权限。")
    return principal


def actor_ref(request: Request, claimed_role=None):
    auth = request.app.state.auth
    if not auth.enabled:
        if not claimed_role:
            raise HTTPException(422, "本地演示需要明确操作角色。")
        return "local-demo:" + claimed_role
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise HTTPException(401, "请先登录。")
    if claimed_role is not None and claimed_role != principal.role:
        raise HTTPException(403, "请求角色与登录身份不一致。")
    return "tenant:" + principal.tenant_id + ":local-login:" + principal.user_ref + ":" + principal.role
