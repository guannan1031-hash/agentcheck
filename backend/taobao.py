"""Configuration boundary for the official Taobao/Tmall integration.

This module deliberately does not use browser cookies, private endpoints, or
unapproved message automation.  Credentials stay in the deployment environment
until the merchant has completed official TOP application and OAuth approval.
"""

import os
from urllib.parse import urlparse


REQUIRED = ("CS_TAOBAO_APP_KEY", "CS_TAOBAO_APP_SECRET", "CS_TAOBAO_CALLBACK_URL")

SYNTHETIC_ORDERS = (
    {"reference": "demo-order-001", "items": [{"sku": "MOCHA-400", "name": "摩可纳咖啡 400g", "quantity": 1}],
     "order_status": "已发货", "logistics_status": "运输中", "logistics_summary": "合成轨迹：包裹已由承运商揽收，等待下一节点。",
     "refund_status": "无退款申请", "updated_at": "2026-09-19T09:30:00+08:00"},
    {"reference": "demo-order-002", "items": [{"sku": "OAT-500", "name": "原味燕麦片 500g", "quantity": 2}],
     "order_status": "交易完成", "logistics_status": "已签收", "logistics_summary": "合成轨迹：承运商已记录签收状态。",
     "refund_status": "退款处理中", "updated_at": "2026-09-19T10:15:00+08:00"},
)


def status():
    callback = os.environ.get("CS_TAOBAO_CALLBACK_URL", "").strip()
    parsed = urlparse(callback)
    callback_valid = bool(parsed.scheme == "https" and parsed.netloc and not parsed.hostname.startswith("127.") if parsed.hostname else False)
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if callback and not callback_valid:
        missing = [name for name in missing if name != "CS_TAOBAO_CALLBACK_URL"] + ["有效的 HTTPS 回调地址"]
    configured = not missing
    return {
        "id": "taobao_tmall",
        "name": "淘宝 / 天猫官方接入",
        "status": "待商家授权" if configured else "待配置",
        "receive": False,
        "send": "disabled_until_verified",
        "configured": configured,
        "requirements": missing or ["创建官方应用并申请订单、物流、退款及客服消息所需权限", "商家完成 OAuth 授权", "在沙盒和真实店铺分别验证收发、人工接管与结果查询"],
    }


def list_synthetic_orders():
    return [{key: value for key, value in order.items() if key != "items"} for order in SYNTHETIC_ORDERS]


def synthetic_order(reference):
    return next((order for order in SYNTHETIC_ORDERS if order["reference"] == reference), None)
