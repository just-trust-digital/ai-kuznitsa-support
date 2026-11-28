"""Validate current basket context and resolve requests that refer to it."""

import re


SKU = re.compile(r"[A-Za-z0-9_.-]{1,128}")
PRICE = re.compile(r"\b(?:цен[ауые]|стоимост\w*|сколько\s+(?:(?:это|теперь|сейчас)\s+)*стоит|"
                   r"сумм[ауе]|итого|рассчита\w*|посчита\w*)\b")
TAX = re.compile(r"\bндс\b")
TAX_PRICE = re.compile(r"цен[ауые]\s+(?:уже\s+)?(?:с|без)\s+ндс|(?:включ\w*|входит)\s+ндс")
DOCUMENT_VERB = r"(?:сделайте|подготовьте|оформите|сформируйте|выставьте|пришлите|дайте|нужен|нужно)"
DOCUMENT_NAME = r"(?:кп|коммерческо\w* предложени\w*|счет(?:а|у)?)"
DOCUMENT = re.compile(rf"(?<!не )\b{DOCUMENT_VERB}\s+(?:(?:мне|нам|пожалуйста)\s+)?({DOCUMENT_NAME})\b")
ANOTHER_DOCUMENT = re.compile(rf"\s+и\s+({DOCUMENT_NAME})\b")
EXISTING_ORDER = re.compile(r"[^?.!\n]{0,60}\bпо\s+(?:предыдущ\w*|стар\w*|оплачен\w*|оформлен\w*)\s+заказ\w*")
SINGLE = re.compile(r"\b(?:эт(?:от|у|а)\s+(?:товар|позици\w*|лицензи\w*)|на\s+эт(?:от|у))\b")


def valid_basket(context):
    if not isinstance(context, dict) or context.get("type") != "basket" or context.keys() - {"type", "items", "selected_offer_id"}:
        return False
    if "items" not in context:
        return "selected_offer_id" not in context
    items = context["items"]
    if not isinstance(items, list) or len(items) > 20:
        return False
    seen = set()
    for line in items:
        if not isinstance(line, dict) or line.keys() != {"offer_id", "quantity"}:
            return False
        sku, quantity = line["offer_id"], line["quantity"]
        if not isinstance(sku, str) or not SKU.fullmatch(sku) or sku in seen or type(quantity) is not int or not 1 <= quantity <= 100000:
            return False
        seen.add(sku)
    selected = context.get("selected_offer_id")
    return "selected_offer_id" not in context or isinstance(selected, str) and selected in seen


def basket_request(text, context, facets):
    if (not context or context.get("type") != "basket" or facets.get("product")
            or facets.get("article") or facets.get("articles")):
        return None
    text = text.casefold().replace("ё", "е")
    documents = []
    for match in DOCUMENT.finditer(text):
        if EXISTING_ORDER.match(text, match.end()):
            continue
        other = ANOTHER_DOCUMENT.match(text, match.end())
        for word in [match[1], *([other[1]] if other else [])]:
            action = "invoice" if word.startswith("счет") else "proposal"
            if action not in documents:
                documents.append(action)
    tax = bool(TAX.search(text))
    price = bool(PRICE.search(TAX_PRICE.sub(" ", text)))
    if tax or price or documents:
        return {"tax": tax, "price": price, "documents": documents, "single": bool(SINGLE.search(text))}
    return None
