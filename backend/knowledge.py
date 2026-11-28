"""Short, sourced answers for common Store questions. Personal orders are mock data."""
import re

STORE = "https://store.softline.ru/delivery_info/"
CABINET = "https://docs.softline.com/ru-ru/lk"
LICENSE = "https://docs.softline.com/ru-ru/lk/fulfillment"
INVOICES = "https://docs.softline.com/ru-ru/lk/invoices"
DOCUMENTS = "https://docs.softline.com/ru-ru/lk/documents-overview"
POWER_BI_PAGE = {"type": "family", "family": "microsoft-power-bi"}
POWER_BI_SOURCE = "https://store.softline.ru/microsoft/microsoft-power-bi/"
POWER_BI_NAME = re.compile(r"(?:(?:microsoft|ms)\s+)?power\s*bi", re.I)
OFFICE_BUSINESS_PAGE = {"type": "family", "family": "microsoft-office-dlya-doma-i-biznesa-2024"}
OFFICE_BUSINESS_NAME = "Microsoft Office для дома и бизнеса 2024"


def asks_edi_status(question):
    for clause in re.split(r"[?!\n]", question.casefold().replace("ё", "е")):
        if (re.search(r"\bэдо\b", clause) and re.search(r"\b(?:нас|нам|наш\w*|мы|со мной|с вами)\b", clause)
                and re.search(r"провери|подключен|подключил|есть|работает|настроен", clause)):
            return True
    return False
PURCHASE_DETAILS = re.compile(
    r"\b(?:куп(?:ить|лю)|приобре(?:ст|т)\w*|покупк\w*|прода(?:ется|ете|жа|жи)|цен[ауые]|стоимост\w*|"
    r"сколько стоит|наличи\w*|закуп\w*|заказать)\b"
)


def power_bi_purchase_topics(text, facets, page_context=None):
    product = facets.get("product", "")
    if facets.get("article") or facets.get("articles") or facets.get("products"):
        return set()
    if product and not POWER_BI_NAME.fullmatch(product):
        return set()
    if not product and page_context != POWER_BI_PAGE:
        return set()
    topics = set()
    for clause in re.split(r"\n+|(?<=[.?!])\s+", text.casefold().replace("ё", "е")):
        purchase = PURCHASE_DETAILS.search(clause)
        if re.search(r"визуализац", clause):
            if purchase or re.search(r"платн", clause):
                topics.add("visuals")
            if purchase and re.search(r"лицензи\w*|подписк\w*", clause):
                topics.add("license")
        elif purchase:
            topics.add("license")
    return topics

INVOICE_LOOKUP = re.compile(
    r"(?:где|найти|скачать|посмотреть).{0,22}счет|"
    r"(?:не вижу|не отобража\w*|не виден).{0,25}счет|"
    r"счет.{0,15}(?:не отобража\w*|не виден|пропал)"
)


def answers_from_knowledge(question):
    text = question.casefold().replace("ё", "е")
    clauses = re.split(r"[?\n]+", text)
    return list(dict.fromkeys(answer for clause in clauses for answer in _clause_answers(clause)))


def _clause_answers(text):
    answers = []
    if re.search(r"физ(?:иц|ическ\w*)\s+лиц|самозанят", text):
        answers.append(("Заказ может оформить физическое лицо: добавьте товар в корзину и укажите свои контакты. "
                "Отдельные условия для самозанятых менеджер может проверить по запросу.", STORE, "checkout"))
    if re.search(r"\bндс\b", text):
        answers.append(("Укажите артикул: НДС нужно подтвердить для конкретной позиции и условий покупки.", STORE, "tax"))
    if re.search(r"закрывающ|бухгалтерск.{0,12}документ|\bупд\b|\bэдо\b", text):
        answers.append(("В личном кабинете откройте нужный заказ. Для юридического лица магазин предоставляет "
                "бухгалтерские документы по заказу. Если нужны документы через ЭДО, передайте менеджеру "
                "название оператора ЭДО и реквизиты компании.", "https://docs.softline.com/ru-ru/lk/closing-documents", "post_purchase"))
    if re.search(r"где\s+(?:ключ|дистрибутив)|(?:найти|получить|скачать|ждать|не приш).{0,35}(?:ключ|дистрибутив)|"
                 r"(?:ключ|дистрибутив).{0,25}(?:не приш|не получ|не виж|не отображ)|"
                 r"когда.{0,30}(?:пришл|прид|получ|будет).{0,22}(?:ключ|дистрибутив)|"
                 r"(?:где|найти|скачать).{0,35}лицензионн\w+ (?:данн|информац)|"
                 r"(?:не получил|не получен|не приш|не отображ|не виж).{0,35}(?:лицензи\w*|ключ|дистрибутив)", text):
        answers.append(("Откройте «Дистрибутивы и ключи» в личном кабинете. Если ищете лицензию по заказу, "
                "откройте «Счета и платежи», найдите заказ и нажмите «К продукту» у нужной позиции.", LICENSE, "post_purchase"))
    if re.search(r"(?:статус|состояни).{0,12}заказ|где.{0,15}(?:мой|наш) заказ", text):
        answers.append(("Откройте ссылку из письма о заказе или выберите покупку в личном кабинете. "
                "На стенде статус вашей тестовой покупки виден в разделе заказов.", CABINET, "post_purchase"))
    if INVOICE_LOOKUP.search(text):
        answers.append(("В личном кабинете откройте «Счета и платежи» и найдите счёт по номеру заказа. "
                "По умолчанию видны заказы, оформленные на почту вашей учётной записи. "
                "Заказы компании видны при подключённом доступе к ним.", INVOICES, "post_purchase"))
    if re.search(r"(?:какие|какой комплект).{0,12}документы.{0,40}(?:заказ|покуп|постав|лицензи)|"
                 r"(?:заказ|покуп|постав).{0,30}какие.{0,12}документы", text):
        answers.append(("По заказам юридических лиц счёт и закрывающие документы доступны в личном кабинете. "
                "Для физлица документы можно запросить у менеджера по номеру заказа.", DOCUMENTS, "post_purchase"))
    if re.search(r"(?:как|можно ли).{0,12}продлить|продление (?:моей|нашей) лицензии", text):
        answers.append(("Для проверки продления укажите продукт и срок действующей лицензии.", CABINET, "renewal"))
    if re.search(r"(?:как|порядок).{0,15}(?:оформить|сделать)\s+(?:онлайн[- ]?)?заказ|"
                 r"\bкак\s+(?:(?:мне|нам|у вас|можно)\s+){0,2}(?:купить|приобрести)\b|"
                 r"как.{0,12}заказать.{0,30}(?:на сайте|в магазин|через корзину)", text):
        answers.append(("Добавьте товар в корзину, нажмите «Оформить заказ», заполните контакты "
                "и выберите плательщика. Для заказа на организацию понадобятся её реквизиты.", STORE, "checkout"))
    if re.search(r"(?:достав|отправ).{0,50}(?:росси[июи]|регион|област\w*|\bрф\b)|"
                 r"(?:достав|отправ)\w*\s+(?:в|до|по)\s+[а-яё]{3,}\b|"
                 r"(?:росси[июи]|регион|област\w*|\bрф\b).{0,50}(?:достав|отправ)", text):
        answers.append(("Физические товары доставляют по России. Стоимость рассчитывается при оформлении "
                "по составу заказа и региону.", STORE, "shipping"))
    if re.search(r"(?:как|каким способом|куда).{0,15}(?:доставляют|доставляется|отправляют).{0,22}(?:лиценз|ключ)|"
                 r"(?:процесс|порядок|как происходит).{0,20}(?:доставк|поставк)|"
                 r"(?:лиценз|ключ).{0,20}(?:по почте|на почту|на e[- ]?mail)|"
                 r"(?:способ|вариант|вид)\w*\s+доставки", text):
        answers.append(("Электронную лицензию отправляют на почту, указанную в заказе. Для оборудования "
                "и коробочного ПО доступны курьер и пункт выдачи.", STORE, "shipping"))
    if re.search(r"срок.{0,15}(?:доставк|поставк\w*\s+(?:лиценз|ключ))|как (?:быстро|долго).{0,15}достав|"
                 r"как\s+(?:быстро|скоро|долго).{0,60}(?:прид|получ|ключ|лиценз)|"
                 r"(?:сколько ждать|через какое время|когда).{0,60}(?:ключ|электронн\w+ лицензи)", text):
        answers.append(("Справка магазина указывает для электронных лицензий диапазон от 5 минут до 30 рабочих дней. "
                "Срок конкретной позиции указан в её карточке.", STORE, "shipping"))
    return answers


def answer_from_knowledge(question):
    """Compatibility helper for callers expecting one result."""
    answers = answers_from_knowledge(question)
    return answers[0] if answers else None
