"""Resolve the customer for one request independently of the jury's demo selector."""
import hashlib
import re
from contextvars import ContextVar


request_customer = ContextVar("request_customer", default=None)


def customer_id(state):
    return request_customer.get() or state.get("buyer_id", "buyer1")


def guest_id(token):
    if isinstance(token, str) and re.fullmatch(r"guest\.[a-f0-9]{64}", token):
        return "visitor-" + hashlib.sha256(token.encode("ascii")).hexdigest()
    return None


def is_guest(state):
    return customer_id(state).startswith("visitor-")


def personal_request(text):
    text = text.casefold().replace("ё", "е")
    return bool(re.search(
        r"\bsl-\d+\b|статус\w*\s+(?:мо\w+\s+)?(?:заказ|счет)|"
        r"(?:мой|моя|мое|мои|моего|моему|нашего|наш|наши)\s+(?:заказ|счет|ключ|лицензи|документ)|"
        r"(?:пришлите|покажите|отправьте|мне нужен|выставьте|оформите|дайте)\s+(?:мне\s+)?(?:счет|ключ|кп|акт|документ)|"
        r"(?:где|когда)\s+(?:же\s+)?(?:мой\s+)?(?:заказ|ключ)|"
        r"(?:не приш|не получ|не вижу).{0,25}(?:ключ|дистрибутив|счет)|"
        r"(?:статус\s+эдо|подключен\w*\s+(?:ли\s+)?(?:у\s+\w+\s+)?эдо)|"
        r"эдо\s+(?:у\s+\w+\s+)?подключен", text))
