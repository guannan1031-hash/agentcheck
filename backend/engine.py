"""Deterministic retrieval/calculation and template model stub; no LLM calls."""

from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from .safety import risky

SCENARIOS = {
    "compare": {"title": "两款燕麦，怎么选？", "question": "原味燕麦和可可燕麦哪个单价更低？我不喜欢甜口。", "intent": "compare", "skus": ["SKU-A", "SKU-B"], "risk": False},
    "batch": {"title": "这批是什么生产日期？", "question": "现在购买原味燕麦，发货的生产日期是哪一天？", "intent": "batch", "skus": ["SKU-A"], "risk": False},
    "risk": {"title": "包装鼓起，要求赔付", "question": "包装鼓起来了，怀疑食品有问题，我要求赔偿。", "intent": "aftersales", "skus": ["SKU-A"], "risk": True},
}


def per_100g(price: str, grams: int) -> Decimal:
    if grams <= 0 or Decimal(price) < 0:
        raise ValueError("Invalid product price or weight")
    return (Decimal(price) * 100 / Decimal(grams)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def answer_question(question: str, catalog: dict, version: int) -> dict:
    result = {"state": "needs_clarification", "text": "请进一步说明要咨询的商品及具体问题，我会核对资料后答复。", "evidence": [], "calculations": [], "missing": ["明确商品及问题"], "model": "template-stub-v1", "risk_code": None}
    if risky(question):
        result.update(state="blocked", text="该问题涉及食品、售后或指令风险，需要人工核实处理；不作安全、疗效或赔付承诺。", risk_code="RISK_REQUIRES_HUMAN", missing=[])
        result["evidence"] = [{"label": r["id"], "content": r["text"], "source": catalog["source"], "version": version} for r in catalog["rules"] if r["id"] == "R-RISK"]
        return result
    if catalog.get("valid_until") and date.fromisoformat(catalog["valid_until"]) < date.today():
        result.update(state="blocked", text="知识已过期，请主管更新后再回复。", risk_code="EXPIRED_KNOWLEDGE")
        return result
    def matches_product(product):
        if product["name"] in question or product["sku"] in question:
            return True
        name_pairs = {product["name"][index:index + 2] for index in range(len(product["name"]) - 1)}
        question_pairs = {question[index:index + 2] for index in range(len(question) - 1)}
        return len(name_pairs & question_pairs) >= 2

    products = [p for p in catalog["products"] if matches_product(p)]
    result["evidence"] = [{"label": p["sku"], "content": f'{p["name"]}：{p["grams"]}g，展示价{p["price"]}元，配料：{p["ingredients"]}；保质期{p["shelf_life_months"]}个月；{p["storage"]}。', "source": catalog["source"], "version": version} for p in products]
    if "生产日期" in question or "批次" in question:
        result.update(text="现有商品资料无法确认当前发货批次和生产日期。请提供包装批次信息；如尚未购买，需要人工核实当前发货批次后答复。", missing=["当前发货批次", "生产日期"])
        return result
    if len(products) == 2 and any(w in question for w in ("比较", "哪个", "单价", "怎么选")):
        price_rule = next((r for r in catalog["rules"] if r["id"] == "R-PRICE"), None)
        if price_rule is None:
            result.update(state="blocked", text="缺少价格比较规则，请主管确认后再回复。", risk_code="MISSING_RULE")
            return result
        result["evidence"].append({"label": price_rule["id"], "content": price_rule["text"], "source": catalog["source"], "version": version})
        for p in products:
            result["calculations"].append({"sku": p["sku"], "formula": f'{p["price"]} ÷ {p["grams"]} × 100', "value": str(per_100g(p["price"], p["grams"])), "unit": "元/100g"})
        values = [Decimal(c["value"]) for c in result["calculations"]]
        detail = "；".join(f'{p["name"]}{p["grams"]}g售价{p["price"]}元，折合{c["value"]}元/100g' for p, c in zip(products, result["calculations"]))
        comparison = "两款单价相同。" if values[0] == values[1] else products[values.index(min(values))]["name"] + "单价更低。"
        taste = ""
        if "不喜欢甜" in question:
            candidates = [p for p in products if "白砂糖" not in p["ingredients"] and p["taste"] == "原味"]
            if len(candidates) == 1:
                taste = f'结合您的口味偏好，可优先考虑{candidates[0]["name"]}；依据是商品原味标注，不代表无糖或医疗适用保证。'
        result.update(state="reviewable", text=detail + "。" + comparison + taste + "以上按展示价计算，未计优惠。", missing=[])
        return result
    if len(products) == 1 and any(w in question for w in ("配料", "规格", "多少克", "保存", "保质期", "多少钱")):
        result.update(state="reviewable", text=result["evidence"][0]["content"], missing=[])
        return result
    pairs = {question[i:i+2] for i in range(len(question)-1)}
    ranked = sorted(catalog["rules"], key=lambda r: sum(pair in r["text"] for pair in pairs), reverse=True)
    for rule in ranked[:3]:
        if sum(pair in rule["text"] for pair in pairs) >= 2:
            result["evidence"].append({"label": rule["id"], "content": rule["text"], "source": catalog["source"], "version": version})
    return result
