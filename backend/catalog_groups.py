"""Names and request filters for product groups from the catalog feed."""

import re

from .selection import license_months


EDITIONS = {
    "Server": r"\b(?:server|сервер\w*)\b",
    "Workstation": r"\b(?:workstation|рабоч\w+\s+станци\w*)\b",
    "Professional": r"\b(?:professional|pro|профессиональн\w*)\b",
    "Standard": r"\b(?:standard|стандартн\w*)\b",
    "Enterprise": r"\benterprise\b",
    "SystemBuilder": r"\bsystem\s*builder\b",
    "Datacenter": r"\bdatacenter\b",
    "Personal": r"\bpersonal\b",
    "Business": r"\bbusiness\b",
}


def product_title(offer):
    name, vendor = offer["name"].strip(), offer.get("vendor", "")
    prefix = vendor + " "
    if vendor and name.casefold().startswith(prefix.casefold()):
        name = name[len(prefix):]
    return name.rstrip(" ,")


def group_name(offers):
    names = [product_title(offer).split() for offer in offers]
    shared = []
    for words in zip(*names):
        if len({word.casefold() for word in words}) != 1:
            break
        shared.append(words[0])
    title = " ".join(shared).rstrip(" (,:")
    return title or product_title(offers[0])


def editions(text):
    return {name for name, pattern in EDITIONS.items() if re.search(pattern, text, re.I)}


def computer_count(text):
    if re.search(r"\bsingle computer\b", text, re.I):
        return 1
    count = re.search(r"\b(\d+)\s*[- ]?\s*(?:ПК|компьютер\w*|computers?)\b", text, re.I)
    return int(count[1]) if count else None


def edition_question(options):
    meanings = {"Server": "для серверов", "Workstation": "для рабочих станций",
                "Professional": "профессиональная редакция", "Standard": "стандартная редакция"}
    details = ". ".join(f"{name} ({meanings[name]})" for name in options if name in meanings)
    return (details + ". " if details else "") + "Какая редакция вам нужна?"


def update_request(text, facets, parameters):
    edition = editions(text)
    if edition:
        parameters["editions"] = sorted(edition)
    version = re.search(r"\bверси\w*\s+(\d+(?:\.\d+)?)\b", text, re.I)
    if version:
        parameters["version"] = version[1]
    count = re.search(r"\b(\d{1,6})\s*шт\b", text, re.I) or re.fullmatch(r"\s*(\d{1,6})\s*[.!?]?\s*", text)
    quantity = facets.get("quantity") or (int(count[1]) if count else None)
    if quantity:
        parameters["quantity"] = quantity
    for key in ("platforms", "architecture", "duration"):
        if facets.get(key):
            parameters[key] = facets[key]


def matches(offer, parameters):
    name = offer["name"]
    if parameters.get("computers") and computer_count(name) != parameters["computers"]:
        return False
    if not set(parameters.get("editions", ())).issubset(editions(name)):
        return False
    version = parameters.get("version")
    if version and not re.search(rf"(?<![\w.]){re.escape(version)}(?![\w.])", name):
        return False
    duration = parameters.get("duration")
    if duration:
        terms = [name, *offer.get("params", {}).get("Срок действия", ())]
        known = set().union(*(license_months(term) for term in terms))
        if not known or known != license_months(duration):
            return False
    quantity = parameters.get("quantity")
    return not quantity or offer.get("min_quantity", 1) <= quantity <= offer.get("max_quantity", 100000)


def followup(text, offers):
    """Answer about the displayed shortlist using the current catalog rows."""
    if not offers:
        return None
    if re.search(r"отлич|разниц|сравни", text, re.I):
        fields = sorted(set().union(*(o.get("params", {}).keys() for o in offers)))
        differing = [key for key in fields if len({tuple(o.get("params", {}).get(key, [])) for o in offers}) > 1]
        descriptions = []
        for offer in offers:
            facts = [f"{key}: {', '.join(offer['params'][key])}" for key in differing if offer.get("params", {}).get(key)]
            detail = " ".join(re.sub(r"<[^>]*>", " ", offer.get("description", "")).split())
            if detail and detail.casefold() not in product_title(offer).casefold():
                facts.append(detail)
            if facts:
                descriptions.append(product_title(offer) + ": " + ". ".join(facts))
        if len(descriptions) != len(offers):
            descriptions.append("Для полного сравнения не хватает характеристик в каталоге.")
        return "\n".join(descriptions)
    if re.search(r"цен[ауые]|сколько сто(?:ит|ят)|стоимость", text, re.I):
        if all(offer["price"] <= 0 for offer in offers):
            return "Цена по запросу для всех показанных позиций. Выберите нужную позицию и нажмите «Запросить цену»."
        return "\n".join(f"{product_title(o)}: " + (f"{o['price']:g} {o['currency']}" if o["price"] > 0
                          else "цена по запросу") for o in offers)
    return None
