"""Explicitly configured DingTalk/Feishu webhook delivery with no stored credentials."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


ALLOWED_HOSTS = {
    "dingtalk": {"oapi.dingtalk.com"},
    "feishu": {"open.feishu.cn", "open.larksuite.com"},
}
TARGET_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass
class DeliveryError(Exception):
    message: str
    unknown: bool = False

    def __str__(self):
        return self.message


def _append_query(url, values):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class NotificationConnector:
    def __init__(self, targets=None, transport=None, clock=None):
        self.targets = self._validate_targets(targets if targets is not None else self._load_targets())
        self.transport = transport
        self.clock = clock or time.time

    @staticmethod
    def _load_targets():
        raw = os.environ.get("CS_NOTIFICATION_TARGETS_JSON", "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("CS_NOTIFICATION_TARGETS_JSON 不是有效JSON。") from error
        if not isinstance(value, dict):
            raise ValueError("CS_NOTIFICATION_TARGETS_JSON 顶层必须是对象。")
        return value

    @staticmethod
    def _validate_targets(value):
        cleaned = {}
        for channel, targets in value.items():
            if channel not in ALLOWED_HOSTS or not isinstance(targets, dict):
                raise ValueError("通知连接器仅支持 dingtalk/feishu 的目标映射。")
            cleaned[channel] = {}
            for target_ref, config in targets.items():
                if not TARGET_REF.fullmatch(target_ref) or not isinstance(config, dict):
                    raise ValueError("通知目标别名格式无效。")
                url = config.get("url")
                secret = config.get("secret", "")
                if not isinstance(url, str) or not isinstance(secret, str):
                    raise ValueError("通知目标配置格式无效。")
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS[channel] or parts.username or parts.password:
                    raise ValueError(f"{channel} 通知地址必须使用官方 HTTPS 域名。")
                if channel == "dingtalk" and parts.path != "/robot/send":
                    raise ValueError("钉钉通知地址路径必须是 /robot/send。")
                if channel == "feishu" and not parts.path.startswith("/open-apis/bot/v2/hook/"):
                    raise ValueError("飞书通知地址必须是自定义机器人 Webhook。")
                cleaned[channel][target_ref] = {"url": url, "secret": secret}
        return cleaned

    def channel_state(self):
        names = {"dingtalk": "钉钉群机器人", "feishu": "飞书群机器人"}
        return [
            {"id": channel, "name": names[channel], "configured": bool(self.targets.get(channel)),
             "targets": sorted(self.targets.get(channel, {}))}
            for channel in ("dingtalk", "feishu")
        ]

    def configured(self, channel, target_ref):
        return target_ref in self.targets.get(channel, {})

    def send(self, channel, target_ref, text, delivery_id):
        config = self.targets.get(channel, {}).get(target_ref)
        if not config:
            raise DeliveryError("该通知目标尚未配置。")
        timestamp_ms = int(self.clock() * 1000)
        url = config["url"]
        if channel == "dingtalk":
            if config["secret"]:
                material = f"{timestamp_ms}\n{config['secret']}".encode()
                signature = base64.b64encode(hmac.new(config["secret"].encode(), material, hashlib.sha256).digest()).decode()
                url = _append_query(url, {"timestamp": str(timestamp_ms), "sign": signature})
            payload = {"msgtype": "text", "text": {"content": text}}
        else:
            payload = {"msg_type": "text", "content": {"text": text}}
            if config["secret"]:
                timestamp = str(timestamp_ms // 1000)
                material = f"{timestamp}\n{config['secret']}".encode()
                signature = base64.b64encode(hmac.new(material, digestmod=hashlib.sha256).digest()).decode()
                payload.update({"timestamp": timestamp, "sign": signature})
        try:
            with httpx.Client(transport=self.transport, timeout=8.0, follow_redirects=False, trust_env=False) as client:
                response = client.post(url, json=payload, headers={"X-CS-Agent-Delivery-ID": delivery_id})
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise DeliveryError("连接结果未知，请先在目标群核对，禁止直接重试。", unknown=True) from error
        if response.status_code >= 500:
            raise DeliveryError(f"平台返回 HTTP {response.status_code}，送达状态未知，请先核对。", unknown=True)
        if response.status_code >= 400:
            raise DeliveryError(f"平台拒绝请求（HTTP {response.status_code}）。")
        try:
            body = response.json()
        except ValueError as error:
            raise DeliveryError("平台响应无法确认，送达状态未知，请先核对。", unknown=True) from error
        code = body.get("errcode") if channel == "dingtalk" else body.get("StatusCode", body.get("code"))
        if code != 0:
            raise DeliveryError(f"平台拒绝消息（业务码 {code}）。")
        receipt_material = f"{channel}:{response.status_code}:{code}:{delivery_id}"
        return {"http_status": response.status_code, "platform_code": str(code),
                "receipt_ref": hashlib.sha256(receipt_material.encode()).hexdigest()[:20]}
