"""Defense-in-depth filters for explicitly synthetic/redacted local test inputs."""
import re

PRIVATE = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:手机号|身份证|收货地址|订单号|API.?KEY|TOKEN|COOKIE)\s*[:：=]\s*\S+", re.I)
RISK = re.compile(r"赔[偿付]|退款|退钱|投诉|起诉|律师|食品安全|发霉|霉变|异味|变味|鼓包|鼓起|异物|中毒|腹泻|过敏|孕妇|婴儿|婴幼儿|治疗|降血糖|功效|身份证|改地址|手机号")
INJECTION = re.compile(r"忽略.{0,12}(规则|指令)|系统提示|system\s*prompt|执行.{0,8}(命令|代码)|泄露|API.?KEY|TOKEN|COOKIE", re.I)


def contains_private(value):
    return bool(PRIVATE.search(value))


def risky(value):
    return bool(RISK.search(value) or INJECTION.search(value))
