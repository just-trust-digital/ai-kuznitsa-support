"""Customer-facing prompts and reusable, non-personal support instructions."""

import re

from .selection import facets_for


QUESTIONS = {
    "seats": ("Сколько рабочих мест или лицензий нужно?", ["1", "5", "10", "Пока не знаю"]),
    "platforms": ("Какая операционная система используется?", ["Windows", "Linux", "macOS", "Пока не знаю"]),
    "payment": ("Как планируете оплатить покупку?", ["По счёту", "Картой", "Пока не знаю"]),
}


def price_prompt(dialog, *, starting=False):
    details = dialog.setdefault("price_details", {})
    pending = dialog.pop("price_field", None)
    for message in dialog["messages"]:
        if message["role"] != "buyer" or message.get("price_card"):
            continue
        text = message["text"]
        fields = facets_for(text)
        count = fields.get("seats") or fields.get("quantity")
        if count:
            details["seats"] = str(count)
        if fields.get("platforms"):
            details["platforms"] = ", ".join(fields["platforms"])
        payment = re.search(r"по сч[её]ту|безнал\w*|(?:оплат\w*\s+)?карт(?:ой|ою)|наличными", text, re.I)
        if payment:
            details["payment"] = payment[0]
    facets = dialog.get("facets", {})
    if facets.get("seats") or facets.get("quantity"):
        details.setdefault("seats", str(facets.get("seats") or facets["quantity"]))
    if facets.get("platforms"):
        details.setdefault("platforms", ", ".join(facets["platforms"]))
    if pending and not starting:
        answer = dialog["messages"][-1]["text"].strip()
        if pending not in details and answer:
            if (pending != "seats" or re.fullmatch(r"[1-9]\d{0,4}|пока не знаю", answer, re.I)):
                details[pending] = answer
    for field, (question, options) in QUESTIONS.items():
        if field not in details:
            dialog["price_field"] = field
            return question, options
    return "Спасибо, менеджер увидит ваши ответы в этом чате.", []


# Only reusable directions through public-facing UI enter shared knowledge.
# Never copy free-form correspondence, account identifiers or commercial terms.
NAVIGATION = set("""откройте перейдите выберите нажмите скачайте сохраните измените включите отключите
    в во из и на затем после этого меню раздел разделе вкладку вкладке кнопку личный личном
    кабинет кабинете профиля профиль настройки настройках уведомления уведомлений уведомление
    язык языка интерфейса рассылки подписки подписку подписок историю история обращений
    заказы мои заказов документы документа дистрибутивы ключи лицензии лицензий
    загрузки файл файлы оплату счета платежи параметры изменить сохранить изменения""".split())
TOPICS = {
    "язык": ("язык",), "уведомления": ("уведомлен", "рассыл"),
    "обращения": ("обращени", "переписк"), "подписки": ("подписк",),
}


def support_topic(question):
    words = question.casefold()
    if re.search(r"\d|@|https?:|\b(?:для|ООО|ИП)\b|не могу|не работает", words, re.I):
        return ""
    topics = [key for key, variants in TOPICS.items() if any(v in words for v in variants)]
    action = next((label for label, pattern in (
        ("off", "отключ|отпис"), ("on", "включ|подписаться"), ("edit", "измен|смен|помен|настро"),
        ("find", "где|найти|посмотр|откры"),) if re.search(pattern, words)), "")
    if not action:
        return ""
    if not topics:
        words = re.sub(r"\b(?:как|где|можно|мне|пожалуйста|подскажите|открыть|найти|посмотреть|изменить|поменять|сменить)\b", "", words)
        topics = sorted(set(re.findall(r"[а-яёa-z]{3,}", words)))
    return "/".join([action, *topics]) if topics else ""


def is_navigation_answer(text):
    return (isinstance(text, str) and bool(re.match(r"^(Откройте|Перейдите|Выберите|Нажмите)\b", text, re.I))
            and not re.search(r"[^а-яёА-ЯЁ\s.,:«»→-]", text)
            and set(re.findall(r"[а-яё]+", text.casefold())) <= NAVIGATION)


def remember_reply(state, dialog):
    messages = dialog["messages"]
    last = messages[-1]
    answer = last["text"].strip()
    if not is_navigation_answer(answer):
        return
    question = next((m["text"] for m in reversed(messages[:-1]) if m["role"] == "buyer"
                     and not re.fullmatch(r"(?:позовите |подключите )?(?:менеджера?|оператора?)[.! ]*",
                                          m["text"], re.I)), "")
    topic = support_topic(question)
    if topic:
        state.setdefault("support_answers", {})[topic] = dict(text=answer, message_id=last["id"])
