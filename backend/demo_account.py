"""Profile-scoped fictional licences used by the local stand."""
import hashlib
import re

from .customer import customer_id
from .knowledge import LICENSE
from .selection import named_products, normalize_brand

PROFILE_STATUS = {"buyer1": "active", "buyer2": "expired", "personal": "missing"}
CONTRACTS = {"buyer1": True, "buyer2": False}
DEMO_REFERENCE = re.compile(r"\bDEMO-(?:LIC|KEY)-[A-Z0-9-]+", re.I)
NUMBER_REFERENCE = re.compile(r"(?:номер\s+лицензии|лицензионный\s+номер|лицензия\s*№)\s*[:№#]?\s*([\w-]*\d[\w-]*)", re.I)


def license_lookup(text, dialog=None):
    folded = text.casefold().replace("ё", "е")
    previous = (dialog or {}).get("decision", {})
    if previous.get("intent") == "license_lookup" and previous.get("next_action") == "clarify":
        if DEMO_REFERENCE.fullmatch(text.strip()) or NUMBER_REFERENCE.fullmatch(text.strip()) or folded.strip() == "[номер]":
            return True
        if any(normalize_brand(folded.strip(" .!?")) == family for _, _, family in named_products(normalize_brand(folded))):
            return True
    if DEMO_REFERENCE.search(text) or NUMBER_REFERENCE.search(text):
        return True
    if not re.search(r"\b(?:ключ\w*|лицензионн\w*\s+номер|номер\s+лицензии|демо-лицензи\w*)\b", folded):
        return False
    retrieval = re.search(r"найти|найд[ие]|не вижу|не приш|не получ|потеря|пришлите|покажите|проверьте|получить|где|просроч|истек", folded)
    personal = re.search(r"\[номер\]|приобрел|приобретал|покупал|купил|\bмо(?:й|ей|ю|его)\b|нашей|потерял|не могу|опла(?:чен|тил)|по счету", folded)
    return bool(retrieval and personal)


def contract_lookup(text):
    folded = text.casefold()
    return bool(re.search(r"\bдоговор\w*", folded) and re.search(
        r"есть ли у (?:нас|меня|нашей)|у нас есть|заключ[её]н ли|статус (?:нашего|моего) договора|наш договор действует", folded))


def licenses(buyer):
    if buyer not in PROFILE_STATUS:
        return []
    result = []
    for product, state in (("Dr.Web", PROFILE_STATUS[buyer]), ("Microsoft Office", "active")):
        if state == "missing":
            continue
        code = hashlib.sha256(f"stand/{buyer}/{product}".encode()).hexdigest()[:8].upper()
        record = dict(id="DEMO-LIC-"+code, name=product, status=state)
        if state == "active":
            record["key"] = "DEMO-KEY-"+code
        result.append(record)
    return result


def license_answer(state, text):
    inventory = licenses(customer_id(state))
    refs = set(ref.upper() for ref in DEMO_REFERENCE.findall(text))
    products = {product for _, _, product in named_products(normalize_brand(text.casefold()))}
    candidates = inventory
    number = NUMBER_REFERENCE.search(text)
    if number:
        refs.add(number[1].upper())
    if refs:
        candidates = [row for row in candidates if refs <= {row["id"], row.get("key")}]
    if products:
        candidates = [row for row in candidates if any(product in normalize_brand(row["name"].casefold()) for product in products)]
    elif not refs and "[номер]" in text.casefold():
        # The masked reference in a replay denotes the sample antivirus purchase.
        candidates = [row for row in inventory if row["name"] == "Dr.Web"]
    elif not refs:
        return "В личном кабинете откройте «Дистрибутивы и ключи». Для поиска укажите номер лицензии или название купленного продукта.", "clarify", LICENSE
    where = "В личном кабинете откройте «Дистрибутивы и ключи»."
    if len(candidates) > 1:
        return "Ключ какой лицензии вам нужен?", "clarify", None
    if not candidates:
        return "В покупках вашего профиля такой лицензии нет. Менеджер проверит заказ.", "handoff", None
    record = candidates[0]
    if record["status"] == "expired":
        return f"Срок лицензии {record['name']} истёк. {where} Для продолжения работы потребуется продление.", "lookup", LICENSE
    if re.search(r"номер\s+лицензии|лицензионный\s+номер", text, re.I) and "ключ" not in text.casefold():
        return f"Номер вашей лицензии {record['name']}: {record['id']}. {where}", "lookup", LICENSE
    return f"Ваш ключ {record['name']}: {record['key']}. {where}", "lookup", LICENSE
