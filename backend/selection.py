"""Text signals for catalogue selection and purchase requests."""

import re

from .knowledge import INVOICE_LOOKUP

PRODUCT_PATTERNS = (
    r"o\s*&\s*o",
    r"ред\s+база\s+данных",
    r"альт\s+рабочая\s+станция",
    r"альт\s+сервер(?:\s+сп)?",
    r"ред\s+ос",
    r"астра\s+линукс",
    r"astra\s+linux",
    r"мойофис",
    r"(?:крипто[-\s]?про|crypto\s?pro)\s+[cс][sс][pрп]",
    r"(?:крипто[-\s]?про|crypto\s?pro)\s+ngate",
    r"(?:крипто[-\s]?про|cryptopro)",
    r"[cс][sс][pрп]",
    r"компас[-\s]?3[дd]",
    r"iru\s+office",
    r"windows\s+server",
    r"windows",
    r"(?:microsoft\s+)?office(?!\s+365)",
    r"(?:microsoft\s+)?(?:office|microsoft)\s+365",
    r"anydesk",
    r"(?:google\s+)?workspace",
    r"gmail",
    r"рутокен",
    r"mdaemon(?:\s+messaging\s+server)?",
    r"kaspersky\s+(?:endpoint\s+security|security\s+center|secure\s+mobility\s+management|"
    r"secure\s+mail\s+gateway|security\s+for\s+mail\s+server)",
    r"kaspersky(?:\s+(?!(?:windows|linux|macos|pc|desktop)\b)[a-z][a-z0-9-]*){0,5}",
    r"касперск\w*",
    r"dr\.?\s*web",
    r"microsoft\s+power\s+bi",
    r"adobe\s+acrobat",
    r"contentreader\s+pdf",
    r"delta\s+design",
    r"revit",
    r"nanocad",
)
EDITION = r"\b(standard|datacenter|business|professional|enterprise|pro|стандартн\w*)\b"
ARCHITECTURE = r"\b(x86[-_]64|x64|arm64|aarch64|arm|эльбрус(?:[-\s]?[\w]+)?)\b"
ARTICLE = re.compile(r"\b(?:артикул(?:ами|а|у|ом|е|ы|ов)?|sku|код[ \t]+позиции)\b"
                     r"(?:[ \t]*[:№#][ \t]*|[ \t]+)([a-z0-9][a-z0-9._-]{1,126}[a-z0-9_])(?![\w-])", re.I)
NUMBER_WORDS = dict(один=1, одна=1, две=2, два=2, три=3, четыре=4,
                    пять=5, шесть=6, семь=7, восемь=8, девять=9, десять=10)
COUNT = r"(\d{1,5}|" + "|".join(NUMBER_WORDS) + r")"
QUANTITY = r"\b" + COUNT + r"\s+"
COUNT_MODIFIERS = r"(?:(?:годов|бессрочн|облачн|локальн|электронн|серверн)\w*\s+){0,3}"
DESKTOP = r"\bпк\b|\bкомпьютер\w*\b|рабочи[ех]\s+станц|\b(?:desktop|workstations?)\b"
PLATFORMS = {"linux": r"linux|линукс", "windows": r"windows|виндовс", "macos": r"mac\s?os|макос"}


def normalize_brand(text):
    text = re.sub(r"(?i)\bo\s*&(?:amp;)*\s*o\b", "O&O", text)
    text = re.sub(r"(?i)\b(?:dr|doctor|doktor|доктор|др)[.\s-]*(?:web|веб|вэб|уэб)\b", "dr.web", text)
    text = re.sub(r"(?i)\bred[\s-]+os\b", "ред ос", text)
    return re.sub(r"(?i)\b(?:kasperskiy|kasperskii|касперский)\b", "kaspersky", text)

_LATIN = str.maketrans(dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch _ y _ e yu ya".split())))


def search_key(text):
    return normalize_brand(text).casefold().translate(_LATIN).replace("_", "")


def catalogue_text(text):
    text = normalize_brand(text)
    text = re.sub(r"(?im)^\s*(?:с уважением|best regards|regards)[\s,.!]*?$[\s\S]*", " ", text)
    text = re.sub(r"(?im)^\s*(?:e-?mail|contact|контакт\w*|почта|телефон|подпись)\s*:[^\n]*", " ", text)
    text = re.sub(r"(?i)\b(?:https?://|www\.)[^\s<>]+|[\w.+-]+@[\w.-]+\.[a-zа-я]{2,}", " ", text)
    text = re.sub(r"\[\s*[\w -]{1,40}\s*\]|<\s*(?:e-?mail|phone|contact|url|тел|номер)\s*>", " ", text, flags=re.I)
    return re.sub(r"\bкас\s+перск", "касперск", text, flags=re.I)


def quantity(match):
    value = match[1]
    return int(value) if value.isdecimal() else NUMBER_WORDS[value]


def license_months(text):
    return {
        quantity(match) * (12 if match[2].startswith(("год", "лет")) else 1)
        for match in re.finditer(QUANTITY + r"(год(?:а|ов)?|лет|месяц(?:а|ев)?|мес)\b", text.casefold())
    }


def facets_for(text):
    text = catalogue_text(text)
    articles = list(dict.fromkeys(match[1].casefold() for match in ARTICLE.finditer(text)))
    if articles:
        text = ARTICLE.sub(" ", text)
    folded = text.casefold().replace("ё", "е")
    facets = {}
    seats = re.search(QUANTITY + COUNT_MODIFIERS
                      + r"(?:пользовател|устройств|сотрудник|компьютер|подключени|мест\b|рабочих мест|"
                        r"рабочих станц|персональн\w*\s+компьютер|планшет|пк\b|кпк\b)", folded)
    items = re.search(QUANTITY + COUNT_MODIFIERS + r"(?:лиценз|экземпляр|штук|планшет|подключени)", folded)
    if not items:
        items = re.search(r"\bлиценз\w*\s+(?:(?:надо|нужн[оы]|требуется)\s+)?" + COUNT + r"\b", folded)
    cores = re.search(QUANTITY + r"(?:ядр|ядер)", folded)
    for key, match in (("seats", seats), ("quantity", items), ("cores", cores)):
        if match:
            facets[key] = quantity(match)
    if re.search(r"\b(?:планшет\w*|кпк)\b", folded):
        facets["device_type"] = "mobile"
    elif re.search(DESKTOP, folded):
        facets["device_type"] = "desktop"
    seat_range = re.search(r"\b(\d{1,5})\s*[–—-]\s*(\d{1,5})\s*(?:пользовател|мест|рабочих мест|устройств)", folded)
    if seat_range and int(seat_range[1]) <= int(seat_range[2]):
        facets["seat_range"] = [int(seat_range[1]), int(seat_range[2])]
        facets.pop("seats", None)
    if re.search(r"\bvdi\b|виртуальн\w+ рабоч\w+\s+стол", folded):
        facets["deployment"] = "vdi"
    if re.search(r"\bофисн\w*\s+пакет|\bпакет\w*\s+офисн", folded):
        facets["topic"] = "office_suite"
    if re.search(r"месячн|ежемесячн|\bна\s+(?:один|1)\s+месяц", folded):
        facets["billing_period"] = "monthly"
    elif re.search(r"годов|ежегодн|\bна\s+(?:один|1)\s+год", folded):
        facets["billing_period"] = "yearly"
    if re.search(r"сам\w+\s+дешев|минимальн\w+\s+цен|дешевле\s+всего", folded):
        facets["value_preference"] = "lowest_price"
    if re.search(r"home\s*(?:&|and)\s*business|для\s+дома\s+и\s+бизнеса", folded):
        facets["edition"] = "home_business"
    if "некоммерческ" in folded or re.search(r"для дома\b(?!\s+и\s+бизнеса)|домашн\w*\s+(?:офис|использован)", folded):
        facets["purpose"] = "noncommercial"
    if "бессроч" in folded:
        facets["license_term"] = "compare" if re.search(r"\bили\b", folded) else "perpetual"
    duration = re.search(QUANTITY + r"(?:год(?:а|ов)?|лет|месяц(?:а|ев)?)\b", folded)
    if duration:
        facets["duration"] = duration[0].strip()
    for key, pattern in (("architecture", ARCHITECTURE), ("edition", EDITION)):
        match = re.search(pattern, folded)
        if match:
            value = match[1]
            if key not in facets:
                facets[key] = "standard" if value.startswith("стандартн") else value
    if re.search(r"\b(?:не|без)\s+(?:поставки\s+)?oem\b", folded):
        facets["oem"] = "excluded"
    elif re.search(r"\boem\b", folded):
        facets["oem"] = "required"
    if "коробоч" in folded:
        facets["delivery"] = "box"
    if re.search(r"\b(?:бюджетн|государственн|муниципальн)\w*\s+(?:организац|учрежден|заказчик|учреждение)", folded):
        facets["buyer_type"] = "budget"
    elif "самозанят" in folded:
        facets["buyer_type"] = "self_employed"
    elif "физлиц" in folded or "физическ" in folded:
        facets["buyer_type"] = "individual"
    elif re.search(r"\bшкол\w*|образовательн|учебн", folded):
        facets["buyer_type"] = "education"
    elif "организац" in folded or re.search(r"\bюр(?:идическ\w*|\.)?\s*лиц", folded):
        facets["buyer_type"] = "organization"
    if re.search(r"коммерческ\w*\s+(?:организац|компани)", folded):
        facets["organization_type"] = "commercial"
    if re.search(r"иностранн\w*\s+(?:организац|лиц|граждан|покупател)", folded):
        facets["jurisdiction"] = "foreign"
    if re.search(r"надстройк|дополнени|плагин|расширени", folded):
        facets["accessory"] = True
    if re.search(r"продлен|renewal", folded):
        facets["license_operation"] = "renewal"
    elif re.search(r"обновлен|upgrade", folded):
        facets["license_operation"] = "upgrade"
    elif re.search(
        r"\bнов(?:ая|ую|ой|ые|ых)\s+(?:(?:бессроч\w+|годов\w+|электрон\w+)\s+){0,2}лиценз|"
        r"\bперв(?:ая|ую)\s+покупк", folded
    ):
        facets["license_operation"] = "new"
    budget = re.search(r"(?:до|бюджет)\s+([\d ]{2,12})\s*(?:руб|₽)", folded)
    if budget:
        facets["budget_limit"] = int(budget[1].replace(" ", ""))
    identifier = re.search(r"\b(?:номер\s+)?(?:заявк[аиуе]|обращени[ея]|запрос[ау]?)\s*[:№#]?\s*"
                           r"([a-zа-я]+[-_]\d+|\d{3,})\b", folded)
    if identifier:
        facets["request_number"] = identifier[1].upper()
    if len(articles) == 1:
        facets["article"] = articles[0]
    elif articles:
        facets["articles"] = articles
    product, versions = product_for(text)
    platforms = [platform for platform, pattern in PLATFORMS.items()
                 if re.search(rf"\b(?:для|под|на)\s+(?:ос\s+)?(?:{pattern})\b", folded)]
    if product in {"криптопро csp", "криптопро", "csp", "kaspersky", "kaspersky endpoint security"}:
        platforms.extend(platform for platform, pattern in PLATFORMS.items()
                         if re.search(rf"\b(?:{pattern})\b", folded))
    if platforms:
        facets["platforms"] = sorted(set(platforms))
    groups = [match[2] for match in named_products(folded)]
    if len(groups) > 1:
        facets["products"] = groups
    request_prefix = facets.get("request_number", "").split("-")[0]
    if product and product.casefold() != request_prefix.casefold():
        facets["product"] = product
        quantified = re.search(r"\bна\s+" + QUANTITY + re.escape(product.casefold()) + r"\b", folded)
        if quantified and "quantity" not in facets:
            facets["quantity"] = quantity(quantified)
    if versions:
        facets["versions"] = versions
    return facets


def named_products(folded):
    matches = []
    for pattern in PRODUCT_PATTERNS:
        for match in re.finditer(r"\b" + pattern + r"\b", folded):
            if any(start < match.end() and match.start() < end for start, end, _ in matches):
                continue
            matches.append((match.start(), match.end(), canonical_product(match[0])))
    matches.sort()
    result = []
    seen = set()
    for start, end, family in matches:
        if family not in seen:
            result.append((start, end, family))
            seen.add(family)
    if len(result) > 1:
        result = [match for match in result if not (
            match[2] == "windows" and re.search(r"\b(?:для|на|под)\s+(?:ос\s+)?$", folded[:match[0]])
        )] or result
    return result


def canonical_product(product):
    if re.fullmatch(r"(?:крипто[-\s]?про|crypto\s?pro)\s+[cс][sс][pрп]", product):
        return "криптопро csp"
    if re.fullmatch(r"(?:крипто[-\s]?про|crypto\s?pro)\s+ngate", product):
        return "криптопро ngate"
    if re.fullmatch(r"крипто[-\s]?про|cryptopro", product):
        return "криптопро"
    if re.fullmatch(r"[cс][sс][pрп]", product):
        return "csp"
    if product.startswith("касперск"):
        return "kaspersky"
    return product


def product_for(text):
    text = catalogue_text(text)
    folded = text.casefold().replace("ё", "е")
    named = named_products(folded)
    if named:
        _, end, product = named[0]
        after = re.sub(r"^\s*(?:standard|datacenter|business|professional|enterprise|pro|"
                       r"home\s*(?:&|and)\s*business|для\s+дома\s+и\s+бизнеса)\b",
                       "", folded[end:])
        edition_or_version = re.match(r"\s*(\d+(?:\.\d+)?)(?:\s*(?:или|/|и)\s*(\d+(?:\.\d+)?))?\b"
                                      r"(?!\s*(?:пользовател|устройств|мест\b|лиценз|подключени|сотрудник))", after)
        versions = [part for part in edition_or_version.groups() if part] if edition_or_version else []
        return product, versions
    quote = re.search(r"«([^»]+)»", text)
    if quote:
        return quote[1], []
    stop = {"standard", "professional", "pro", "enterprise", "business", "oem", "sku", "x64", "arm64",
            "email", "mail", "contact", "contacts", "phone", "tel", "url", "name", "signature", "redacted"}
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9]+", text) if word.casefold() not in stop][:3]
    if words:
        return " ".join(words), []
    return "", []


def classify(text):
    folded = text.casefold().replace("ё", "е")
    if re.search(r"(тендер|постоплат|отсроч|рассроч|закупк\w*\s+(?:среди|по|конкурентн)|конкурентн\w*\s+закупк|"
                 r"запрос котиров|закупочн\w* процедур|\bс[м]?мп\b|\b(?:44|223)[-\s]?фз)", folded):
        return "special_terms"
    if re.search(r"\bот\s+(?:пао|ооо|ао)\b|поставщик|с кем заключ|юрлиц[оа]\s+продавц", folded):
        return "supplier"
    if "скид" in folded and re.search(r"переход|миграц|смен\w+\s+вендор|замен\w+\s+лиценз", folded):
        return "migration_discount"
    if re.search(r"\bакци[яию]\b|\bпромокод\b|(?:персональн\w*|индивидуальн\w*)\s+скид|"
                 r"скид\w*.*(?:повторн|раньше|ранее|уже покуп)|(?:повторн|прежн)\w*\s+покуп\w*.*скид", folded):
        return "promotion_terms"
    if re.search(r"смен\w+\s+вендор|переход\w*\s+с\s+\w+\s+на", folded):
        return "supplier"
    if re.search(r"перепродаж|реселлер|партнерск\w+\s+(?:канал|договор|программ)", folded):
        return "resale_terms"
    if re.search(r"оплатил|ключ\s+лиценз|закрывающ|дистрибутив|(?:статус|состояни).{0,12}заказ", folded):
        return ""
    if re.search(r"статус|не отвеч|нет ответа|ответа .*нет|нет связи|новост\w*.*(?:прежн|запрос|обращ)|"
                 r"(?:вчера|ранее|прежн|уже)\w*\s+.{0,42}(?:запрос\w* цен|заявк|обращен)|"
                 r"не дозво", folded):
        return "request_status"
    if re.search(r"контакт\w* менеджер|как связаться|телефон\s+(?:менеджер|поддержк)", folded):
        return "contact"
    if re.search(r"\bндс\b", folded):
        return ""
    if re.search(r"\b(?:тз|техзадани[ея])\b|техническ\w+ задан", folded):
        return "technical_requirements"
    if re.search(r"техподдерж|техническ\w* поддерж|поддержк\w*\s+(?:включена|входит|какая)", folded):
        return "support_terms"
    if re.search(r"\b(?:в|из|номер|по)\s+реестр\w*|регистрационн\w+\s+номер\s+реестр", folded):
        return "registry"
    if re.search(r"совместим|будет\s+работать\s+на|одновременн\w*\s+сеанс|"
                 r"(?:сколько|можно|предел|лимит).{0,45}(?:установ|подключ|активац)|"
                 r"доступ\w*\s+к\s+\d+\s+(?:компьютер|машин|устройств)", folded):
        return "compatibility"
    if re.search(r"(?:выстав\w*|оформ\w*|пришл\w*|подготов\w*|направ\w*|сдела\w*|нуж\w*|получ\w*)"
                 r"\s+(?:мне\s+|нам\s+)?(?:кп\b|коммерческ\w+ предлож)|"
                 r"^\s*(?:кп\b|коммерческ\w+ предлож\w*)\s+на\b|"
                 r"(?:выстав\w*|оформ\w*|пришл\w*|сдела\w*|нуж\w*)\s+(?:мне\s+|нам\s+)?счет", folded):
        return "document"
    if any(re.search(r"\bсчет\s+(?:на|по)\b", clause) and not INVOICE_LOOKUP.search(clause)
           for clause in re.split(r"[?\n]+", folded)):
        return "document"
    if re.search(r"\b(?:gmail|(?:google\s+)?workspace)\b", folded) and re.search(r"тариф|подписк|стоит|цен", folded):
        return "mail_subscription"
    return ""
