import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from . import demo_account
from .customer import customer_id, is_guest, personal_request
from .storage import Storage
from .documents import PROFILES, UNKNOWN, default_templates, printable_date, render_document
from .basket import basket_request, valid_basket
from . import catalog_groups
from . import reviews, sales_chat
from .knowledge import (OFFICE_BUSINESS_NAME, OFFICE_BUSINESS_PAGE, POWER_BI_PAGE, POWER_BI_SOURCE,
                        answers_from_knowledge, asks_edi_status, power_bi_purchase_topics)
from .integrations import FOLLOW_UP_FIELDS, FOLLOW_UP_TEXT, PROVIDERS, USAGE_FIELDS, follow_up_missing
from .selection import (
    DESKTOP, PLATFORMS, catalogue_text, classify, facets_for, license_months, named_products,
    product_for, normalize_brand, search_key,
)


ROLES = {"buyer", "manager1", "manager2", "developer"}
SAMPLE_FACETS = ("buyer_type", "license_term", "delivery_term")
CATALOG_PARAMS = {
    "Тип поставки", "Способ доставки", "Пользователь", "Совместимая архитектура/платформа",
    "Версия", "Тип лицензии", "Тип организации", "Модель лицензирования", "Срок действия",
    "К-во пользователей до", "К-во узлов", "Разрядность",
    "Срок поставки", "Способ оплаты", "Срок действия КП",
    "Тип продукта", "Назначение", "Компоненты пакетов",
}
MANAGERS = {
    "manager1": {"name": "Менеджер 1", "expertise": ["лицензии", "скидка"], "available": True},
    "manager2": {"name": "Менеджер 2", "expertise": ["заказы", "тендер"], "available": True},
}
KNOWLEDGE = "https://docs.softline.com/ru-ru/lk"
AVAILABILITY_DETAILS = r"\b(?:наличи\w*|доступ(?:н\w*|ен))"
VIEWED_DETAILS = re.compile(r"\b(?:цен[ауые]|сколько сто(?:ит|ят)|стоимост\w*|подробнее|расскажи\w*)|" + AVAILABILITY_DETAILS, re.I)


def identifier(prefix):
    return f"{prefix}-{uuid4().hex[:12]}"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def substantive_buyer_message(message):
    if message.get("role") != "buyer":
        return False
    text = re.sub(r"[^\w\s]+", " ", message["text"].casefold()).strip()
    return bool(text and text not in {"здравствуйте", "привет", "добрый день", "доброе утро",
                                     "добрый вечер", "здравствуй", "спасибо"})


def amount(value, minimum=0, maximum=1000000000):
    try:
        value = Decimal(str(value))
        if value.is_finite() and minimum <= value <= maximum:
            return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        pass
    raise ValueError("Некорректное число")


def string(payload, key, *, optional=False, limit=6000):
    value = payload.get(key, "")
    if isinstance(value, str) and len(value) <= limit and (value.strip() or optional):
        return value.strip()
    raise ValueError(f"Проверьте поле {key}")


def viewed_facets(offer):
    facets = facets_for(offer["name"])
    # A catalogue title describes one SKU. Counts in it are not an order.
    for key in ("products", "quantity"):
        facets.pop(key, None)
    return facets


def offer_record(offer_id, name, price, category, url="", description="", currency="RUB", listed=True,
                 vendor_code="", params=None, vendor="", old_price=None):
    return dict(id=offer_id, name=name, price=float(price), currency=currency,
                available=listed and price > 0, listed=listed, category=category, url=url,
                description=description, vendor_code=vendor_code, params=params or {}, vendor=vendor, old_price=old_price)


FIXTURES = [
    offer_record("demo-mdaemon-20", "MDaemon 20 пользователей", 19200, "Почта"),
    offer_record("demo-kaspersky-50", "Kaspersky Endpoint Security 50 устройств", 48500, "Безопасность",
                 vendor="Kaspersky", params={"Тип продукта": ["софт"], "Тип лицензии": ["Полная версия"],
                                             "Назначение": ["Защита рабочих станций"],
                                             "Совместимая архитектура/платформа": ["Windows"]}),
    offer_record("demo-necessity", "Лицензия корпоративная под заказ", 0, "Лицензии"),
    dict(offer_record("demo-drweb", "Dr.Web для рабочих станций", 1800, "Антивирусы",
                      vendor="Доктор Веб", params={"Тип поставки": ["электронная"], "Срок действия": ["12 мес."],
                                                  "Совместимая архитектура/платформа": ["Windows"]}),
         min_quantity=5, max_quantity=49),
]


def catalog_rows(path):
    if path is None:
        yield from FIXTURES
        return
    stack = []
    categories = {}
    for event, node in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            stack.append(node)
            continue
        tag = node.tag.split("}")[-1]
        if tag == "category" and node.get("id"):
            categories[node.get("id")] = node.text or ""
        if tag == "offer":
            fields = {c.tag.split("}")[-1]: c.text or "" for c in node}
            params = {}
            for item in node:
                label, value = item.get("name"), (item.text or "").strip()
                if item.tag.split("}")[-1] == "param" and label in CATALOG_PARAMS and value:
                    params.setdefault(label, []).append(value[:250])
            for key in ("name", "vendor", "model", "title", "description"):
                if fields.get(key):
                    fields[key] = unescape(fields[key])
            name = fields.get("name") or " ".join(filter(None, (fields.get("vendor"), fields.get("model"))))
            if not name:
                name = fields.get("title") or fields.get("vendorCode")
            if name and node.get("id"):
                price = amount(fields.get("price", "0").replace(",", "."))
                try:
                    old_price = float(amount(fields.get("oldprice", "0").replace(",", ".")))
                except ValueError:
                    old_price = None
                url = fields.get("url", "")
                if urlsplit(url).scheme not in {"https", "http"}:
                    url = ""
                offer = offer_record(node.get("id"), name, price,
                                   categories.get(fields.get("categoryId"), fields.get("categoryId", "")),
                                   url, fields.get("description", "")[:1000],
                                   "RUB" if fields.get("currencyId") in {"", None, "RUR"} else fields["currencyId"],
                                   node.get("available") == "true", fields.get("vendorCode", "").strip(), params,
                                   fields.get("vendor", "").strip(), old_price)
                if node.get("group_id"):
                    offer["group_id"] = node.get("group_id")
                bounds = re.search(r"кол-во\s+от\s+(\d+)(?:\s+до\s+(\d+))?\s+шт", fields.get("sales_notes", ""), re.I)
                if bounds:
                    offer["min_quantity"] = int(bounds[1])
                    if bounds[2]:
                        offer["max_quantity"] = int(bounds[2])
                yield offer
        if tag in {"offer", "category"} and len(stack) > 1:
            stack[-2].remove(node)
        stack.pop()


class Store:
    def __init__(self, db_path, feed_path=None, samples_path=None, engine=None, hybrid_engine=None):
        self.storage = Storage(db_path)
        self.samples = self._read_samples(samples_path)
        self.engine = engine
        self.hybrid_engine = hybrid_engine
        self.business_hours = None
        self.feed_path = Path(feed_path) if feed_path else None
        if self.feed_path is None or self.feed_path.is_file():
            self.import_catalog()

    @staticmethod
    def _read_samples(path):
        if not path or not Path(path).is_file():
            return {}
        samples = {}
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if not re.fullmatch(r"buy_part_\d+_masked\.txt", name):
                    continue
                raw = archive.read(name).decode("utf-8-sig")
                for match in re.finditer(r"^#(\d+) \[([^\n]+?)\] \[([^\]\n]*)\]\s*(.*?)(?=^#\d+ |\Z)", raw, re.M | re.S):
                    number, label, source, text = match.groups()
                    sample_id = f"{Path(name).stem}-{number}-{match.start()}"
                    samples[sample_id] = dict(id=sample_id, number=number, text=text.strip(), source_label=label,
                                              source=source, label="", note="", flags=dict(manual_review=False,
                                              collect_sample=False), facets=dict.fromkeys(SAMPLE_FACETS, ""))
        return samples

    def import_catalog(self):
        source = str(self.feed_path.resolve()) if self.feed_path else "synthetic"
        version = f"{source}:{self.feed_path.stat().st_mtime_ns}:v11" if self.feed_path else f"{source}:v3"
        with self.storage.connect(write=True) as db:
            existing = db.execute("SELECT value FROM catalog WHERE key='version'").fetchone()
            if existing and existing[0] == version:
                return
            db.execute("DELETE FROM offers")
            db.executemany("INSERT INTO offers VALUES (?,?,?,?)", (
                (offer["id"], search_key(offer["name"]), search_key(offer["category"]),
                 json.dumps(offer, ensure_ascii=False)) for offer in catalog_rows(self.feed_path)
            ))
            count = db.execute("SELECT count(*) FROM offers").fetchone()[0]
            if not count:
                raise ValueError("В фиде нет товаров")
            db.executemany("INSERT OR REPLACE INTO catalog VALUES (?,?)",
                           [("version", version), ("source", "xml" if self.feed_path else "synthetic"),
                            ("count", str(count)), ("imported_at", now())])

    @staticmethod
    def _check(role, scope):
        if role not in ROLES or not isinstance(scope, str) or not 0 < len(scope) <= 160:
            raise PermissionError("Недоступная рабочая область")

    def _state(self, db, scope):
        state = self.storage.read(db, scope) or dict(
            day=1, buyer_id="buyer1", managers={key: dict(value) for key, value in MANAGERS.items()},
            dialogs={}, documents={}, templates={}, reviews=[], requests={},
            orders={"SL-1042": dict(id="SL-1042", status="Оплачен", license_status="Ключ готов",
                                    name="Лицензия для корпоративной почты", source=KNOWLEDGE),
                    "SL-1043": dict(id="SL-1043", status="Оплачен", license_status="Ключ ожидается",
                                    name="Лицензия для рабочих станций", source=KNOWLEDGE, buyer_id="buyer2")})
        state["orders"].setdefault("SL-1044", dict(id="SL-1044", status="Оплачен", license_status="Ключ готов",
                                                  name="Лицензия для дома", source=KNOWLEDGE, buyer_id="personal"))
        templates = state["templates"]
        for key, template in default_templates().items():
            if key not in templates:
                if any(item["kind"] == template["kind"] and item["status"] == "published"
                       for item in templates.values()):
                    template["status"] = "retired"
                templates[key] = template
        return state

    @staticmethod
    def _feed(db, state):
        meta = dict(db.execute("SELECT key,value FROM catalog WHERE key != 'version'"))
        return dict(day=state["day"], count=int(meta.get("count", 0)),
                    source=meta.get("source", "missing"), imported_at=meta.get("imported_at"),
                    changes=state.get("changes", []))

    @staticmethod
    def _offer(body, state):
        offer = json.loads(body)
        change = next((item for item in state.get("changes", []) if item["id"] == offer["id"]), None)
        if change:
            offer.update(price=change["price"], available=offer.get("listed", offer["available"]) and change["price"] > 0)
        return offer

    def _search(self, db, state, query="", limit=12, *, by_product=False, offset=0):
        words = re.findall(r"\w+", search_key(query))[:8]
        conditions, args = [], []
        for word in words:
            conditions.append("(name LIKE ? ESCAPE '!' OR category LIKE ? ESCAPE '!')")
            args.extend([f"%{word.replace('_', '!_')}%"] * 2)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        if by_product:
            availability_first = "available DESC, " if words else ""
            rows = db.execute(f"""
                WITH cards AS (
                    SELECT MIN(id) AS id, MAX(json_extract(body,'$.available')) AS available
                    FROM offers {where}
                    GROUP BY COALESCE('group:' || json_extract(body,'$.group_id'), 'offer:' || id)
                    ORDER BY {availability_first}id LIMIT ? OFFSET ?
                )
                SELECT offers.body FROM cards JOIN offers ON offers.id=cards.id
                ORDER BY {availability_first}cards.id
            """, [*args, limit, offset])
            return self._catalog_cards(db, state, [self._offer(row[0], state) for row in rows], limit)
        order = " ORDER BY id"
        if words:
            order = " ORDER BY json_extract(body,'$.available') DESC, id"
        rows = db.execute("SELECT body FROM offers" + where + order + " LIMIT ?", [*args, limit])
        return [self._offer(row[0], state) for row in rows]

    @staticmethod
    def _group_rows(db, group_id):
        return db.execute("SELECT body FROM offers WHERE json_extract(body,'$.group_id')=?", (group_id,))

    def _catalog_cards(self, db, state, offers, limit, offset=0):
        cards, seen = [], set()
        for offer in offers:
            group_id = offer.get("group_id")
            if group_id:
                if group_id in seen:
                    continue
                seen.add(group_id)
            if offset:
                offset -= 1
                continue
            if group_id:
                variants = [json.loads(row[0]) for row in self._group_rows(db, group_id)]
                if len(variants) > 1:
                    offer = {**offer, "group_name": catalog_groups.group_name(variants)}
            cards.append(offer)
            if len(cards) == limit:
                break
        return cards

    def _answer_product_group(self, db, state, dialog, text, incoming):
        offers = [self._offer(row[0], state) for row in self._group_rows(db, dialog["viewed_group"])]
        explicit_product = named_products(catalogue_text(text).casefold())
        if incoming.get("article") or incoming.get("articles") or (explicit_product and incoming.get("product") and
                offers and not any(self._is_primary_product(offer, incoming["product"]) for offer in offers)):
            dialog.pop("viewed_group", None)
            dialog.pop("group_request", None)
            return False
        if not offers:
            dialog.pop("viewed_group", None)
            self._message(dialog, "assistant", "Товар исчез из каталога. Выберите другую карточку.", mode="rules")
            self._decision(dialog, "product_group", "clarify")
            return True

        previous = next((m for m in reversed(dialog["messages"]) if m.get("offers")), {})
        shown_ids = [o["id"] for o in previous.get("offers", []) if o.get("group_recommendation")]
        by_id = {o["id"]: o for o in offers}
        shown = [by_id[i] for i in shown_ids if i in by_id]
        detail = catalog_groups.followup(text, shown)
        if detail:
            self._message(dialog, "assistant", detail, mode="rules", source=self._feed(db, state)["source"])
            self._decision(dialog, "product_group", "inform", evidence=[o["id"] for o in shown])
            return True
        parameters = dialog.setdefault("group_request", {})
        pending = dialog.get("decision", {}).get("missing", [])
        number = re.fullmatch(r"\s*(\d{1,6})\s*[.!]?\s*", text)
        if number and not {"quantity", "computers"}.intersection(pending):
            parameters["ambiguous_number"] = int(number[1])
            self._message(dialog, "assistant", "Это количество лицензий или номер варианта?",
                          choices=["Количество лицензий", "Номер варианта"] if shown else ["Количество лицензий"], mode="rules")
            self._decision(dialog, "product_group", "clarify", missing=["number_meaning"])
            return True
        number = parameters.pop("ambiguous_number", None)
        if number and "number_meaning" in pending:
            if "количество" in text.casefold():
                parameters["quantity"] = number
            elif "вариант" in text.casefold() and 1 <= number <= len(shown):
                offer = shown[number - 1]
                self._message(dialog, "assistant", f"Вы выбрали «{offer['name']}».", offers=[offer], mode="rules")
                self._decision(dialog, "product_group", "inform", evidence=[offer["id"]])
                return True
        capacity = catalog_groups.computer_count(text)
        if number is None and "computers" in pending and text.strip().isdigit():
            capacity = int(text.strip())
        if capacity:
            parameters["computers"] = capacity
        catalog_groups.update_request(text, incoming, parameters)
        if capacity and "quantity" not in pending:
            parameters.pop("quantity", None)
        constraints = {key: parameters[key] for key in ("architecture", "platforms") if key in parameters}
        matches = [offer for offer in offers if catalog_groups.matches(offer, parameters) and
                   self._matches_constraints(offer, constraints)]
        quantity = parameters.get("quantity")
        if quantity:
            matches = [offer for offer in matches if offer.get("min_quantity")] or matches
        if not matches:
            self._message(dialog, "assistant", "Под эти условия в каталоге нет варианта. Уточните редакцию или количество.", mode="rules")
            self._decision(dialog, "product_group", "clarify")
            return True

        names = {catalog_groups.product_title(offer) for offer in matches}
        options = sorted(set().union(*(catalog_groups.editions(name) for name in names)))
        capacities = sorted({n for o in matches if (n := catalog_groups.computer_count(o["name"]))})
        if len(names) > 1 and len(options) > 1 and not parameters.get("editions"):
            prompt = catalog_groups.edition_question(options)
            self._message(dialog, "assistant", prompt, choices=options[:6], mode="rules")
            self._decision(dialog, "product_group", "clarify", missing=["edition"])
        elif not quantity and any(offer.get("min_quantity") for offer in matches):
            self._message(dialog, "assistant", "Сколько лицензий нужно?", mode="rules")
            self._decision(dialog, "product_group", "clarify", missing=["quantity"])
        elif len(capacities) > 1 and not parameters.get("computers"):
            self._message(dialog, "assistant", "На скольких компьютерах вы установите программу?",
                          choices=[f"{n} ПК" for n in capacities[:6]], mode="rules")
            self._decision(dialog, "product_group", "clarify", missing=["computers"])
        else:
            ranked = sorted(matches, key=lambda offer: (not offer["available"], offer["price"], offer["id"]))
            distinct = {}
            for offer in ranked:
                distinct.setdefault(catalog_groups.product_title(offer), offer)
            shortlist = [{**offer, "group_recommendation": True, "suggested_quantity": quantity or offer.get("min_quantity", 1)}
                         for offer in list(distinct.values())[:3]]
            answer = "Подойдёт эта лицензия." if len(shortlist) == 1 else "Вот варианты под ваши условия."
            self._message(dialog, "assistant", answer, offers=shortlist, mode="rules", source=self._feed(db, state)["source"])
            self._decision(dialog, "product_group", "inform" if len(shortlist) == 1 else "shortlist",
                           evidence=[offer["id"] for offer in shortlist])
        return True

    @staticmethod
    def _can_view(role, dialog, state=None):
        if dialog.get("demo_archived"):
            return False
        if role == "buyer":
            return dialog.get("buyer_id", "buyer1") == customer_id(state or {})
        return role == "developer" or dialog["assignment"] == role

    @staticmethod
    def _buyer_view(dialog):
        result = {key: dialog[key] for key in ("id", "assignment", "status", "created_at", "decision", "approved_discount", "offer_email") if key in dialog}
        if dialog.get("route"):
            result["route"] = {"reason": dialog["route"]["reason"]}
        result["messages"] = []
        for message in dialog["messages"]:
            public = {key: message[key] for key in (
                "id", "role", "text", "at", "offers", "mode", "source", "decision", "document_id",
                "evidence", "template_id", "joined", "quote", "attachment", "reference", "synthetic", "choices",
                "show_offer_details",
            ) if key in message}
            if isinstance(message.get("model"), dict):
                public["model"] = {key: message["model"][key] for key in (
                    "mode", "requested_mode", "verified_model", "published",
                ) if key in message["model"]}
            result["messages"].append(public)
        return result

    def _result_view(self, role, result):
        if role == "buyer" and "dialog" in result:
            return {**result, "dialog": self._buyer_view(result["dialog"])}
        return result

    def _check_cached_access(self, role, action, state, payload, result):
        dialog = result.get("dialog")
        dialog_id = payload.get("dialog_id") or (dialog and dialog["id"])
        if not dialog_id:
            return
        current = state["dialogs"][dialog_id]
        allowed = (reviews.can_review(role, current) if action in {"review", "review_replay"}
                   else self._can_view(role, current, state))
        if not allowed:
            raise PermissionError("Диалог недоступен")

    def continue_guest_chat(self, scope, visitor, dialog_id):
        with self.storage.connect(write=True) as db:
            state = self._state(db, scope)
            dialog = state["dialogs"].get(dialog_id)
            if not dialog or dialog.get("demo_archived"):
                raise KeyError(dialog_id)
            owner = customer_id(state)
            if dialog.get("buyer_id") == visitor:
                dialog["buyer_id"] = owner
                self.storage.save(db, scope, state)
            elif dialog.get("buyer_id") != owner:
                raise PermissionError()

    def chat_snapshot(self, role, scope, dialog_id):
        self._check(role, scope)
        with self.storage.connect() as db:
            state = self._state(db, scope)
            dialog = state["dialogs"].get(dialog_id)
            if not dialog or not self._can_view(role, dialog, state):
                return {"dialogs": []}
            return {"dialogs": [self._buyer_view(dialog) if role == "buyer" else self._staff_dialog(dialog, state)]}

    @staticmethod
    def _staff_dialog(dialog, state):
        evidence = {key for m in dialog["messages"] for key in m.get("decision", {}).get("evidence", [])}
        return dict(dialog, linked_orders=[o for o in state["orders"].values()
                                          if o["id"] in evidence and o.get("buyer_id", "buyer1") == dialog.get("buyer_id", "buyer1")])

    def snapshot(self, role, scope):
        self._check(role, scope)
        with self.storage.connect() as db:
            state = self._state(db, scope)
            buyer_id = customer_id(state)
            orders = state["orders"].values() if role == "buyer" else []
            documents = state["documents"].values() if role == "buyer" else []
            feed = self._feed(db, state)
            catalog = self._search(db, state, limit=9, by_product=True) if role == "buyer" else []
            if role == "buyer":
                feed.pop("changes", None)
            return dict(role=role, buyer_id=buyer_id, feed=feed, offers=self._search(db, state),
                        catalog=catalog[:8], catalog_has_more=len(catalog) > 8,
                        licenses=demo_account.licenses(buyer_id) if role == "buyer" else [],
                        manager2_active=scope.startswith("jury") and any(
                            d["status"] == "assigned" and d["assignment"] == "manager2"
                            and not d.get("demo_archived") for d in state["dialogs"].values()),
                        handoff_stats=self._handoff_stats(state) if role != "buyer" or scope.startswith("jury") else {},
                        orders=[o for o in orders if o.get("buyer_id", "buyer1") == buyer_id],
                        documents=[self._document_view(d) for d in documents if d.get("buyer_id", "buyer1") == buyer_id],
                        dialogs=[self._buyer_view(d) if role == "buyer" else self._staff_dialog(d, state)
                                 for d in state["dialogs"].values() if self._can_view(role, d, state)],
                        queue=[dict(id=d["id"], created_at=d["created_at"], reason=d["route"]["reason"])
                               for d in state["dialogs"].values() if d["status"] == "queued"]
                        if role != "buyer" else [],
                        usage={row[0]: dict(zip(USAGE_FIELDS, row[1:])) for row in db.execute(
                            "SELECT provider,calls,metered_calls,input_tokens,output_tokens FROM usage WHERE scope=?",
                            (scope,))} if role == "developer" else {},
                        managers=self._managers(state) if role != "buyer" else {},
                        templates=list(state["templates"].values()) if role != "buyer" else [],
                        samples=[{**value, **state.get("samples", {}).get(key, {})}
                                 for key, value in self._visible_samples(scope).items()] if role != "buyer" else [],
                        journal=[event for event in state.get("journal", [])
                                 if role == "developer" or event["role"] == role
                                 and event["actor"] == (buyer_id if role == "buyer" else role)],
                        review_queue=reviews.queue(state, role),
                        reviews=reviews.log_for(state, role))

    @staticmethod
    def _handoff_stats(state):
        counts = dict(dialogs=0, first_request=0, later_request=0, automatic=0)
        dates = []
        for dialog in state["dialogs"].values():
            if dialog.get("demo_archived"):
                continue
            messages = dialog["messages"]
            if not any(substantive_buyer_message(m) for m in messages):
                continue
            counts["dialogs"] += 1
            dates.append(dialog["created_at"])
            transfers = [m["decision"] for m in messages
                         if m.get("decision", {}).get("next_action") == "handoff"]
            if transfers:
                first = transfers[0]
                category = "automatic"
                if first.get("intent") == "operator":
                    category = "first_request" if first["turn"] == 1 else "later_request"
                counts[category] += 1
        return dict(**counts, period_start=min(dates, default=None), period_end=max(dates, default=None))

    def act(self, role, action, payload, scope):
        self._check(role, scope)
        if not isinstance(payload, dict) or not isinstance(action, str):
            raise ValueError("Ожидаются поля действия")
        for key in ("viewed_offer_id", "viewed_group_id"):
            if key in payload:
                if role != "buyer" or action != "message":
                    raise ValueError("Неизвестный контекст страницы")
                string(payload, key, limit=128)
        if "reference" in payload:
            reference = payload["reference"]
            if (role != "buyer" or action != "message" or payload.get("starter")
                    or not isinstance(reference, dict) or set(reference) != {"type", "id"}
                    or string(reference, "type") not in ("offer", "group", "order", "document", "license")):
                raise ValueError("Не удалось прикрепить позицию")
            string(reference, "id", limit=128)
        if "page_context" in payload:
            context = payload["page_context"]
            if role != "buyer" or action != "message" or (context not in (POWER_BI_PAGE, OFFICE_BUSINESS_PAGE) and not valid_basket(context)):
                raise ValueError("Неизвестный контекст страницы")
        if action == "chat_file":
            with self.storage.connect() as db:
                return self._chat_file(db, self._state(db, scope), scope, role, payload)
        request = string(payload, "request_id", optional=True, limit=128)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        model = self._prepare_model(role, action, payload, scope, request, fingerprint)
        with self.storage.connect(write=True) as db:
            state = self._state(db, scope)
            actor = customer_id(state) if role == "buyer" and action != "buyer_select" else role
            key = f"{actor}/{action}/{request}"
            existing = state["requests"].get(key) if request else None
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("Этот номер запроса уже использован с другими данными")
                self._check_cached_access(role, action, state, payload, existing["result"])
                return self._result_view(role, existing["result"])
            notifications = getattr(self, "manager_notifications", None)
            assigned_before = notifications.assignments(state) if notifications else None
            result = self._act(db, state, role, action, payload, scope, model)
            if notifications:
                new_assignments = notifications.assignments(state) - assigned_before
                notifications.enqueue(db, scope, (manager for _, manager in new_assignments))
            if request:
                state["requests"][key] = dict(fingerprint=fingerprint,
                                              result=json.loads(json.dumps(result, ensure_ascii=False)))
            if action not in {"search", "order_status", "download", "chat_file"}:
                event = dict(action=action, role=role, actor=actor, at=now())
                state["journal"] = [*state.get("journal", [])[-999:], event]
            self.storage.save(db, scope, state)
            return self._result_view(role, result)

    def _prepare_model(self, role, action, payload, scope, request, fingerprint):
        chat = role == "buyer" and action == "message"
        copilot = role != "buyer" and action == "copilot"
        if not chat and not copilot:
            return None
        mode = payload.get("mode", "local" if chat else "rules")
        if mode not in ("rules", "local", "hybrid"):
            raise ValueError("Неизвестный режим ответа")
        if mode == "rules" or (chat and (payload.get("starter") or payload.get("reference"))):
            return None
        text = string(payload, "text", optional=bool(payload.get("attachment"))) if chat else ""
        if chat and not text:
            return None
        if chat and payload.get("viewed_offer_id") and VIEWED_DETAILS.search(text):
            return None
        with self.storage.connect() as db:
            state = self._state(db, scope)
            dialog = None
            actor = customer_id(state)
            request_actor = actor if chat else role
            cached = state["requests"].get(f"{request_actor}/{action}/{request}") if request else None
            if cached:
                if cached["fingerprint"] != fingerprint:
                    raise ValueError("Этот номер запроса уже использован с другими данными")
                return None
            if copilot:
                dialog = self._dialog(state, payload)
                if not self._can_view(role, dialog):
                    raise PermissionError("Диалог назначен другому менеджеру")
                text = self._last_question(dialog)
            elif payload.get("dialog_id"):
                dialog = self._dialog(state, payload)
                self._own(state, dialog)
                if dialog["status"] in {"assigned", "queued"}:
                    return None
            facets, skipped_reply = self._incoming_facets(text, dialog or {})
            if chat and not payload.get("page_context") and not payload.get("viewed_offer_id") and (
                    payload.get("viewed_group_id") or (dialog or {}).get("viewed_group")) and not (
                    facets.get("article") or facets.get("articles")):
                return None
            known_facets = dict((dialog or {}).get('facets', {}))
            skipped = set((dialog or {}).get('optional_answers', {}))
            if skipped_reply:
                skipped.add(skipped_reply)
            switched_product = self._merge_facets(known_facets, facets)
            page_context = payload.get('page_context')
            if chat and (asks_edi_status(text) or demo_account.license_lookup(text, dialog) or demo_account.contract_lookup(text)):
                return None
            if chat and basket_request(catalogue_text(text), page_context, facets):
                return None
            has_viewed_sku = bool((dialog or {}).get('viewed_offer')) and not switched_product and not page_context
            if chat and not has_viewed_sku and power_bi_purchase_topics(catalogue_text(text), facets, page_context):
                return None
            if chat and (facets.get("article") or facets.get("articles")) and VIEWED_DETAILS.search(text):
                return None
            offers = self._conversation_offers(db, state, text, facets, 6)
            local_offers = offers
            if chat and dialog:
                viewed = None if switched_product else self._viewed_offer(db, state, dialog, known_facets)
                if viewed and VIEWED_DETAILS.search(text):
                    local_offers = [viewed] if self._offer_term_matches(viewed, text, known_facets) else []
                elif not offers and not (facets.get('product') or facets.get('article')):
                    local_offers = self._conversation_offers(db, state, text, known_facets, 6)
            else:
                viewed = None
        result = None
        usage = {}
        grounded_id = None
        if mode == "hybrid" and self.hybrid_engine:
            matching = {offer["id"] for offer in offers if offer["id"] in self.hybrid_engine.catalog}
            folded = catalogue_text(text).casefold()
            aliases = {} if facets.get("product") or facets.get("article") else self.hybrid_engine.catalog
            for sku, published in aliases.items():
                name = published.get("name", "").strip().casefold()
                if name and re.search(rf"(?<![\w@.-]){re.escape(name)}(?![\w@-]|\.\w)", folded):
                    matching.add(sku)
            if len(matching) == 1:
                grounded_id = matching.pop()
                adapter = getattr(self.hybrid_engine, "consult", self.hybrid_engine.hybrid) if chat else self.hybrid_engine.hybrid
                result = adapter("technical", grounded_id)
                usage.update(result.get("usage", {}))
        if result is None or not result.get("verified_model"):
            if self.engine:
                evidence = {'known_facets': known_facets,
                            'offers': [{key: offer[key] for key in ("name", "price", "currency", "available")}
                                       for offer in local_offers]}
                if chat:
                    evidence['follow_up_context'] = {
                        'viewed_offer': bool(viewed),
                        'direct_answer': bool(viewed and VIEWED_DETAILS.search(text)),
                        'skipped': [] if switched_product else sorted(skipped),
                    }
                grounded_id = None
                adapter = getattr(self.engine, "consult", self.engine.answer) if chat else self.engine.answer
                result = adapter(text, context=json.dumps(evidence, ensure_ascii=False))
                usage.update(result.get("usage", {}))
                offers = local_offers
        if usage:
            with self.storage.connect(write=True) as db:
                for provider in PROVIDERS:
                    if provider in usage:
                        counters = [usage[provider].get(field) for field in USAGE_FIELDS]
                        if all(type(value) is int and 0 <= value <= 10 ** 12 for value in counters):
                            self.storage.add_usage(db, scope, provider, counters)
        result = result or {"mode": "unconfigured", "verified_model": False}
        return {**result, "requested_mode": mode, "buyer_id": actor, "grounded_id": grounded_id,
                "candidate_ids": [offer["id"] for offer in offers]}

    @staticmethod
    def _render(template, question="", offer=None, extra=None):
        offer = offer or {}
        values = {"question": question, "product_name": offer.get("name", ""),
                  "price": offer.get("price", ""), "currency": offer.get("currency", ""),
                  "article": offer.get("vendor_code") or offer.get("id", "")}
        values.update(extra or {})
        return re.sub(r"\{([a-z_]+)\}", lambda match: str(values[match[1]])
                      if match[1] in values and values[match[1]] not in ("", None) else match[0],
                      template["text"])

    def _visible_samples(self, scope):
        if not scope.startswith("jury"):
            return self.samples
        return {f"demo-{index}": dict(id=f"demo-{index}", text=text, source_label=label,
                                    label="", note="", flags=dict(manual_review=False, collect_sample=False),
                                    facets=dict.fromkeys(SAMPLE_FACETS, ""))
                for index, (text, label) in enumerate([
                    ("Нужна лицензия на 20 пользователей.", "Конфигурация"),
                    ("Можно получить скидку 7%?", "Скидка"),
                    ("Где скачать закрывающие документы?", "Заказ"),
                ], 1)}

    def _chat_file(self, db, state, scope, role, payload):
        dialog = self._dialog(state, payload)
        if not self._can_view(role, dialog, state):
            raise PermissionError()
        message_id = string(payload, "message_id")
        message = next((m for m in dialog["messages"] if m["id"] == message_id and m.get("attachment")), None)
        row = db.execute("SELECT content FROM chat_files WHERE scope=? AND message_id=?", (scope, message_id)).fetchone()
        if message is None or row is None:
            raise KeyError(message_id)
        return {"filename": message["attachment"]["name"], "content": base64.b64encode(row[0]).decode("ascii")}

    def _act(self, db, state, role, action, payload, scope, model):
        if action == "feed_import" and (role == "developer" or (role == "buyer" and scope.startswith("jury"))):
            return self._feed_day(db, state, payload)
        if role == "buyer":
            if is_guest(state) and action not in {"search", "message", "handoff", "end_chat", "price_request"}:
                raise PermissionError("Войдите в личный кабинет.")
            if action == "clear_history" and scope.startswith("jury"):
                for dialog in state["dialogs"].values():
                    if self._can_view(role, dialog, state):
                        dialog.update(demo_archived=True, status="resolved")
                return {}
            if action == "end_chat":
                dialog = self._dialog(state, payload)
                self._own(state, dialog)
                dialog["status"] = "resolved"
                return {"dialog": dialog}
            if action == "buyer_select" and scope.startswith("jury"):
                buyer_id = payload.get("buyer_id")
                if buyer_id not in PROFILES:
                    raise ValueError("Выберите тестового покупателя")
                state["buyer_id"] = buyer_id
                return {"buyer_id": buyer_id}
            if action == "search":
                limit = int(amount(payload.get("limit", 12), 1, 100))
                grouped = payload.get("by_product") is True
                page = payload.get("page", 1)
                if type(page) is not int or not 1 <= page <= 100000:
                    raise ValueError("Проверьте номер страницы")
                offset = (page - 1) * limit
                query = string(payload, "query", optional=True)
                cleaned = catalogue_text(query).strip()
                facets = facets_for(cleaned)
                exact_family = facets.get("product") and named_products(cleaned.casefold())
                if query.strip() and not cleaned:
                    offers = []
                elif exact_family or facets.get("article"):
                    offers = ([] if len(facets.get("products", ())) > 1 else
                              self._compatible_offers(db, state, facets.get("product"), facets,
                                                      limit=None if grouped else limit))
                    if grouped:
                        offers = (offers[offset:offset + limit + 1] if facets.get("article") else
                                  self._catalog_cards(db, state, offers, limit + 1, offset))
                else:
                    offers = self._search(db, state, cleaned, limit + (1 if grouped else 0), by_product=grouped,
                                          offset=offset if grouped else 0)
                return {"offers": offers[:limit], "page": page, "has_more": len(offers) > limit} if grouped else {"offers": offers}
            if action == "order_status":
                order = state["orders"][string(payload, "order_id").upper()]
                self._own(state, order)
                return {"order": order}
            if action in {"message", "handoff"}:
                return self._buyer_chat(db, state, action, payload, model, scope)
            if action in {"invoice", "proposal"}:
                return self._document(db, state, action, payload)
            if action == "price_request":
                return self._price_request(db, state, payload)
        elif role in MANAGERS:
            if action == "availability":
                return self._availability(state, role, payload)
            if action in {"reply", "resolve"}:
                dialog = self._dialog(state, payload)
                if dialog["assignment"] != role:
                    raise PermissionError("Диалог назначен другому менеджеру")
                if action == "reply":
                    if self.business_hours and not self.business_hours():
                        raise ValueError("Рабочие часы менеджера с 09:00 до 19:00 МСК.")
                    self._post_message(db, scope, dialog, role, payload)
                    sales_chat.remember_reply(state, dialog)
                    dialog.pop("price_field", None)
                else:
                    dialog["status"] = "resolved"
                return {"dialog": dialog}
        if role != "buyer":
            if action in {"claim", "reassign"}:
                return self._assign(state, role, action, payload)
            if action == "sample_classify":
                return self._sample_classify(state, role, payload, scope)
            if action == "review":
                return self._review(state, role, payload)
            if action == "review_replay":
                replay = reviews.replay(
                    state, role, string(payload, "dialog_id"), string(payload, "message_id"),
                    string(payload, "version"),
                    lambda scenario, dialog, question: self._replay_answer(db, scenario, dialog, question),
                )
                return {"replay": replay}
            if action == "copilot":
                dialog = self._dialog(state, payload)
                if not self._can_view(role, dialog):
                    raise PermissionError("Диалог назначен другому менеджеру")
                last = self._last_question(dialog)
                offers = self._conversation_offers(db, state, last, facets_for(last), 5)
                text = (f"Проверьте наличие и условия для позиции «{offers[0]['name']}»."
                        if offers else "Уточните артикул или название продукта.")
                if model and model.get("verified_model") and model.get("text"):
                    return dict(text=str(model["text"])[:6000], offers=offers, mode=model["mode"],
                                model=self._model_metadata(model))
                return dict(text=text, offers=offers, mode="rules", model=self._model_metadata(model))
            if action in {"template_draft", "template_emulate", "template_delete"} or (
                role == "developer" and action in {"template_publish", "template_rollback"}
            ):
                return self._template(db, state, action, payload)
        if action == "moderate" and role != "buyer":
            return self._moderate(state, role, payload)
        if action == "download" and role in {"buyer", "developer"}:
            return self._download(state, payload, role)
        raise PermissionError("Действие недоступно для этой роли")

    def _feed_day(self, db, state, payload):
        day = payload.get("day")
        if type(day) is not int or day not in {1, 2}:
            raise ValueError("Выберите день 1 или 2")
        state["day"] = day
        state["changes"] = []
        if day == 2:
            offers = self._search(db, state, "MDaemon", 30) or self._search(db, state, limit=30)
            priced = next((item for item in offers if item["available"]), None)
            if priced:
                state["changes"].append(dict(id=priced["id"], article=priced.get("vendor_code") or priced["id"],
                                             currency=priced["currency"], before=priced["price"],
                                             price=float(amount(Decimal(str(priced["price"])) * Decimal("1.05")))))
        return {"feed": self._feed(db, state)}

    @staticmethod
    def _dialog(state, payload, create=False):
        dialog_id = string(payload, "dialog_id", optional=True)
        if dialog_id or not create:
            dialog = state["dialogs"][dialog_id]
            if dialog.get("demo_archived"):
                raise KeyError(dialog_id)
            return dialog
        dialog_id = identifier("D")
        dialog = dict(id=dialog_id, assignment=None, status="open", route=None,
                      buyer_id=customer_id(state),
                      flags=dict(manual_review=False, collect_sample=False), messages=[], created_at=now())
        state["dialogs"][dialog_id] = dialog
        return dialog

    @staticmethod
    def _message(dialog, role, text, **extra):
        dialog["messages"].append(dict(id=identifier("M"), role=role, text=text, at=now(), **extra))

    def _post_message(self, db, scope, dialog, role, payload, reference=None):
        if dialog["status"] == "resolved":
            raise ValueError("Чат завершён")
        file = payload.get("attachment")
        text = string(payload, "text", optional=bool(file), limit=4000)
        extras = {"reference": reference} if reference else {}
        reply_to = string(payload, "reply_to", optional=True)
        if reply_to:
            original = next((m for m in dialog["messages"] if m["id"] == reply_to), None)
            if original is None:
                raise ValueError("Сообщение для ответа не найдено")
            excerpt = string(payload, "quote", optional=True, limit=240)
            original_text = original["text"] or original.get("attachment", {}).get("name", "")
            original_text = re.sub(r"\bO&(?:amp;)+O\b", "O&O", original_text, flags=re.I)
            if excerpt and excerpt not in original_text:
                raise ValueError("Цитата изменилась")
            extras["quote"] = {"id": reply_to, "role": original["role"], "text": excerpt or original_text[:240]}
        if file:
            if not isinstance(file, dict):
                raise ValueError("Файл не прочитан")
            name = string(file, "name", limit=200).replace("\\", "/").rsplit("/", 1)[-1]
            if not name or any(ord(ch) < 32 for ch in name):
                raise ValueError("Проверьте имя файла")
            try:
                content = base64.b64decode(string(file, "content", limit=1_398_104), validate=True)
            except binascii.Error as error:
                raise ValueError("Файл не прочитан") from error
            if not 0 < len(content) <= 1_048_576:
                raise ValueError("Выберите файл до 1 МБ")
            extras["attachment"] = {"name": name, "size": len(content)}
        self._message(dialog, role, text, **extras)
        if file:
            db.execute("INSERT INTO chat_files VALUES (?,?,?)", (scope, dialog["messages"][-1]["id"], content))
        return text

    @staticmethod
    def _last_question(dialog):
        return next((message["text"] for message in reversed(dialog["messages"]) if message["role"] == "buyer"), "")

    def _assign(self, state, role, action, payload):
        dialog = self._dialog(state, payload)
        if self.business_hours and not self.business_hours():
            raise ValueError("Рабочие часы менеджера с 09:00 до 19:00 МСК.")
        if action == "claim":
            if role not in MANAGERS or dialog["status"] != "queued":
                raise PermissionError("Обращение недоступно для назначения")
            manager = role
        else:
            if dialog["status"] != "assigned" or not self._can_view(role, dialog):
                raise PermissionError("Обращение недоступно для переназначения")
            manager = string(payload, "manager_id")
            if manager not in MANAGERS:
                raise ValueError("Выберите менеджера")
        dialog["assignment"] = manager
        dialog["status"] = "assigned"
        dialog["route"] = {**dialog["route"], "operation": action, "by": role}
        if action == "claim":
            self._message(dialog, "system", "Менеджер подключился.", joined=manager)
        return {"dialog": dialog}

    @staticmethod
    def _managers(state):
        return {key: {**manager, "load": sum(d["assignment"] == key and d["status"] == "assigned"
                                          for d in state["dialogs"].values())}
                for key, manager in state["managers"].items()}

    def _route(self, state, dialog, reason, expertise, *, notification=None):
        managers = self._managers(state)
        hour = (datetime.now(timezone.utc).hour + 3) % 24
        if self.business_hours and not self.business_hours():
            dialog.update(assignment=None, status="queued", offer_email=True,
                          route=dict(reason=reason, expertise=expertise, candidates=managers))
            self._message(dialog, "assistant", "Менеджеры отвечают с 09:00 до 19:00 МСК. Можно отправить запрос на почту.")
            if "цен" in reason.casefold() and expertise == "лицензии":
                question, choices = sales_chat.price_prompt(dialog, starting=True)
                self._message(dialog, "assistant", question, choices=choices, mode="rules")
            return
        if dialog["assignment"] and dialog["status"] == "assigned":
            return
        eligible = [
            (key, manager) for key, manager in managers.items() if manager["available"]
            and (key != "manager2" or reason == "Бронирование по запросу покупателя" and expertise in manager["expertise"])
            and (manager.get("schedule") is None
                 or manager["schedule"][0] <= hour < manager["schedule"][1])
        ]
        ranked = sorted(eligible, key=lambda pair: (expertise not in pair[1]["expertise"], pair[1]["load"], pair[0]))
        assignment = ranked[0][0] if ranked else None
        dialog.update(assignment=assignment, status="assigned" if assignment else "queued",
                      route=dict(reason=reason, expertise=expertise, candidates=managers))
        self._message(dialog, "assistant", (notification or "Передал обращение менеджеру.") if assignment
                      else "Обращение в очереди. Менеджер увидит его, когда будет доступен.")

        if "цен" in reason.casefold() and expertise == "лицензии":
            question, choices = sales_chat.price_prompt(dialog, starting=True)
            self._message(dialog, "assistant", question, choices=choices, mode="rules")

    def _availability(self, state, role, payload):
        available = payload.get("available")
        if type(available) is not bool:
            raise ValueError("Укажите доступность менеджера")
        manager = state["managers"][role]
        manager["available"] = available
        if "expertise" in payload:
            expertise = payload["expertise"]
            if not isinstance(expertise, list) or len(expertise) > 12 or any(
                not isinstance(item, str) or len(item) > 100 for item in expertise
            ):
                raise ValueError("Укажите список специализаций")
            manager["expertise"] = expertise
        if "schedule" in payload:
            hours = payload["schedule"]
            if hours is not None and (not isinstance(hours, list) or len(hours) != 2
                                     or any(type(hour) is not int for hour in hours)
                                     or not 0 <= hours[0] < hours[1] <= 24):
                raise ValueError("Укажите начало и конец смены по московскому времени")
            manager["schedule"] = hours
        for dialog in state["dialogs"].values():
            if dialog["status"] == "queued":
                route = dialog["route"]
                self._route(state, dialog, route["reason"], route["expertise"])
        return {"manager": self._managers(state)[role]}

    @staticmethod
    def _reduced_price(offer):
        return bool(offer.get("available") and offer.get("old_price") and
                    0 < offer["price"] < offer["old_price"])

    def _message_reference(self, db, state, reference):
        kind, ref_id = reference["type"], reference["id"].strip()
        if kind == "offer":
            row = db.execute("SELECT body FROM offers WHERE id=?", (ref_id,)).fetchone()
            record = self._offer(row[0], state) if row else None
        elif kind == "group":
            row = next(self._group_rows(db, ref_id), None)
            record = self._catalog_cards(db, state, [self._offer(row[0], state)], 1)[0] if row else None
        elif is_guest(state):
            raise PermissionError("Войдите в личный кабинет.")
        elif kind == "license":
            record = next((row for row in demo_account.licenses(customer_id(state)) if row["id"] == ref_id), None)
        else:
            record = state["orders" if kind == "order" else "documents"].get(ref_id)
            if record:
                self._own(state, record)
        if record is None:
            raise KeyError("Запись недоступна")
        name = record.get("group_name") or record.get("name") or ref_id
        if kind == "order":
            name = f"Заказ {ref_id} · {name}"
        elif kind == "document":
            document_type = "Счёт" if record["kind"] == "invoice" else "КП"
            name = f"{document_type} {record.get('number') or ref_id}"
        return {"type": kind, "id": ref_id, "label": name}, record

    def _answer_account_reference(self, dialog, kind, record):
        if kind == "order":
            answer = ". ".join(filter(None, (record.get("status"), record.get("license_status")))) + "."
        elif kind == "document":
            answer = f"Сумма документа {record['total']:g} {record['currency']}. Скачать его можно в разделе «Счета и платежи»."
        else:
            status = "Срок лицензии истёк." if record["status"] == "expired" else "Лицензия действует."
            answer = status + " Сведения о лицензии доступны в разделе «Дистрибутивы и ключи»."
        self._message(dialog, "assistant", answer, mode="rules")
        self._decision(dialog, kind, "inform", evidence=[record["id"]])

    def _buyer_chat(self, db, state, action, payload, model, scope):
        dialog = self._dialog(state, payload, create=True)
        self._own(state, dialog)
        if dialog["status"] == "resolved":
            raise ValueError("Чат завершён")
        if model and model["buyer_id"] != dialog["buyer_id"]:
            raise ValueError("Покупатель изменился. Повторите запрос.")
        text = ""
        reference, record = None, None
        if action == "message" or payload.get("text"):
            if payload.get("reference"):
                reference, record = self._message_reference(db, state, payload["reference"])
                payload = {key: value for key, value in payload.items()
                           if key not in ("page_context", "viewed_offer_id", "viewed_group_id")}
                if reference["type"] in ("offer", "group"):
                    payload[f"viewed_{reference['type']}_id"] = reference["id"]
            text = self._post_message(db, scope, dialog, "buyer", payload, reference)
            if payload.get("page_context"):
                dialog["messages"][-1]["page_context"] = payload["page_context"]
            group_id = payload.get("viewed_group_id")
            # A tile visible behind the chat is only a hint for a question about it.
            group_question = VIEWED_DETAILS.search(text) or text.startswith("Помогите подобрать лицензию для «")
            if group_id and (reference or payload.get("starter") or group_question) and (group_id != dialog.get("viewed_group") or text == "Подобрать лицензию"):
                dialog.update(viewed_group=group_id, group_request={})
                dialog.pop("viewed_offer", None)
                dialog.pop("optional_answers", None)
                dialog["facets"] = {}
            elif payload.get("viewed_offer_id"):
                dialog.pop("viewed_group", None)
                dialog.pop("group_request", None)
        if action == "handoff":
            self._route(state, dialog, "Запрос покупателя", "лицензии")
            self._decision(dialog, "operator", "handoff", mandatory=True)
        elif payload.get("starter") and len(dialog["messages"]) == 1:
            starter = payload["starter"]
            if starter == "viewed":
                row = db.execute("SELECT body FROM offers WHERE id=?", (string(payload, "offer_id"),)).fetchone()
                if not row:
                    raise ValueError("Товар больше не доступен")
                offer = self._offer(row[0], state)
                dialog["facets"] = viewed_facets(offer)
                dialog["viewed_offer"] = offer["id"]
                self._message(dialog, "assistant", "Что хотите уточнить по лицензии?", offers=[offer], mode="rules")
                self._decision(dialog, "selection", "inform", evidence=[offer["id"]])
            elif starter in {"search", "choose"}:
                dialog["guided_selection"] = starter == "choose"
                dialog["search_by_name"] = starter == "search"
                prompt = ("Как называется программа, которую вы ищете?" if starter == "search"
                          else "Какую задачу вы хотите решить с помощью программы?")
                self._message(dialog, "assistant", prompt, mode="rules")
                self._decision(dialog, "selection", "clarify", missing=["product" if starter == "search" else "task"])
            else:
                raise ValueError("Неизвестный стартовый вопрос")
        elif dialog.get("offer_email") and text.casefold().strip() == "отправить на почту":
            dialog["offer_email"] = False
            dialog.pop("price_field", None)
            self._message(dialog, "assistant", "Отправлено на почту.", mode="rules")
            self._decision(dialog, "operator", "await_manager")
        elif dialog.get("price_field") and text and not reference:
            question, choices = sales_chat.price_prompt(dialog)
            self._message(dialog, "assistant", question, choices=choices, mode="rules")
            self._decision(dialog, "catalog", "await_manager")
        elif dialog["status"] not in {"assigned", "queued"}:
            if text:
                viewed_id = payload.get("viewed_offer_id")
                if viewed_id and (reference or VIEWED_DETAILS.search(text)):
                    incoming, _ = self._incoming_facets(text, dialog)
                    if not any(incoming.get(key) for key in ("product", "article", "articles")):
                        row = db.execute("SELECT body FROM offers WHERE id=?", (viewed_id,)).fetchone()
                        if row:
                            offer = self._offer(row[0], state)
                            if viewed_id != dialog.get("viewed_offer"):
                                dialog["facets"] = viewed_facets(offer)
                                dialog.pop("optional_answers", None)
                            dialog["viewed_offer"] = viewed_id
                        else:
                            dialog.pop("viewed_offer", None)
                dialog["messages"][-1]["review_context"] = reviews.capture_context(dialog)
                self._answer(db, state, dialog, text, model, payload.get("page_context"),
                             (reference["type"], record) if reference else None)
            else:
                self._message(dialog, "assistant", "Файл добавлен. Напишите, чем помочь.")
                self._decision(dialog, "attachment", "clarify")
        else:
            self._decision(dialog, "follow_up", "await_manager")
        follow_up_field = None
        if model and dialog["messages"][-1].get("role") == "assistant":
            candidate = dialog["messages"][-1]
            candidate["review_variants"] = dict(scenario=reviews.redact(candidate["text"]),
                                                 draft=reviews.redact(str(model.get("generator_text") or model.get("text") or "")[:6000]))
            dialog["messages"][-1]["model"] = self._model_metadata(model)
            follow_up_field = self._publish_consultation(dialog, model)
        last = dialog["messages"][-1]
        if last.get("role") == "assistant":
            if follow_up_field:
                last["decision"]["missing"] = [follow_up_field]
            reduced = [offer for offer in last.get("offers", [])[:4] if self._reduced_price(offer)]
            if reduced:
                prefix = "Хороший выбор. По каталогу цена снижена: " if dialog.get("facets", {}).get("article") and len(reduced) == 1 else "По каталогу цена снижена: "
                last["text"] += "\n" + prefix + "; ".join(offer["name"] for offer in reduced) + "."
        return {"dialog": dialog}

    @staticmethod
    def _publish_consultation(dialog, model):
        message = dialog["messages"][-1]
        decision = message.get("decision", {})
        variants = message.setdefault("review_variants", {})
        if (not model.get("verified_model") or model.get("route") == "operator" or not message.get("offers")
                or decision.get("next_action") != "shortlist"):
            variants["reason"] = ("Модель недоступна." if not model.get("verified_model") else
                "Выбран маршрут к менеджеру." if model.get("route") == "operator" else
                "Модельное уточнение применяется при показе подборки.")
            return
        try:
            question = json.loads(model.get("text", ""))["question"]
        except (ValueError, TypeError, KeyError):
            variants["reason"] = "В ответе модели отсутствует вопрос нужного формата."
            return
        choice = next((key for key, value in FOLLOW_UP_TEXT.items() if value == question), None)
        if not choice or not follow_up_missing(choice, dialog.get('facets', {}), dialog.get('optional_answers', {})):
            variants["reason"] = 'Вопрос не входит в допустимые уточнения или ответ уже известен.'
            return
        grounded_id = model["grounded_id"]
        candidates = [offer for offer in message["offers"] if offer["available"]
                      and (offer["id"] == grounded_id if grounded_id else offer["id"] in model["candidate_ids"])]
        if not candidates:
            variants["reason"] = "У модели нет подходящей доступной позиции."
            return
        offer = candidates[0]
        seats = decision.get("facets", {}).get("seats")
        if seats and not re.search(rf"\b{seats}\b", offer["name"]):
            variants["reason"] = "Карточка не подтверждает нужное количество мест."
            return
        qualification = "\n" + message["text"] if decision.get("intent") == "security" or message.get("template_id") else ""
        code = offer.get("vendor_code")
        reference = f"артикул {code}" if code else f"код в каталоге {offer['id']}"
        message["text"] = f"{question}\nВариант из каталога: {offer['name']} ({reference}).{qualification}"
        message["mode"] = model["mode"]
        message["model"]["published"] = True
        message["evidence"] = dict(offer_id=offer["id"], vendor_code=code, url=offer["url"],
                                   source=message.get("source"))
        return FOLLOW_UP_FIELDS[choice]

    @staticmethod
    def _model_metadata(model):
        return {key: model[key] for key in ("mode", "requested_mode", "verified_model", "route", "error",
                                           "decision_intent", "decision_probability", "decision_version",
                                           "follow_up_choice", "follow_up_version")
                if key in model} if model else None

    @staticmethod
    def _decision(dialog, intent, action, *, missing=(), evidence=(), mandatory=False):
        decision = dict(intent=intent, facets=dict(dialog.get("facets", {})), missing=list(missing),
                        next_action=action, evidence=list(evidence), mandatory=mandatory,
                        turn=sum(substantive_buyer_message(message) for message in dialog["messages"]))
        dialog["decision"] = decision
        dialog["messages"][-1]["decision"] = decision

    def _replay_answer(self, db, state, dialog, question):
        reference = question.get("reference")
        resolved = None
        if reference:
            _, record = self._message_reference(db, state, reference)
            resolved = (reference["type"], record)
        self._answer(db, state, dialog, question["text"], page_context=question.get("page_context"), reference=resolved)

    def _answer(self, db, state, dialog, text, model=None, page_context=None, reference=None):
        previous = dialog.get("decision", {})
        missing = previous.get("missing", [])
        # Older conversations stored a task prompt as refine_search without a field.
        legacy_task = previous.get("next_action") == "refine_search" and not missing and any(
            m.get("role") == "assistant" and m.get("text", "").endswith("Опишите, для чего нужна программа.")
            for m in dialog["messages"][-2:])
        if not reference and ("task" in missing or legacy_task):
            dialog["guided_selection"] = True
            for key in ("viewed_group", "group_request", "viewed_offer"):
                dialog.pop(key, None)
            facets = dialog.setdefault("facets", {})
            if legacy_task or previous.get("next_action") == "refine_search":
                for key in ("product", "products", "article", "articles", "topic"):
                    facets.pop(key, None)
            facets["task"] = text
        self._answer_request(db, state, dialog, text, model, page_context, reference)
        decision = dialog.get("decision", {})
        if (decision.get("next_action") == "clarify" and previous.get("next_action") == "clarify"
                and decision.get("intent") in {"selection", "unclear", "security"}
                and missing and missing == decision.get("missing") and "minimum_criterion" not in missing
                and previous.get("intent") == decision["intent"] and previous.get("facets") == decision["facets"]):
            # Preserve the unresolved field and its choices so the next reply still has a referent.
            reply = dialog["messages"][-1]
            choices = reply.setdefault("choices", [])
            if dialog.get("guided_selection") and missing in (["seats"], ["platform"]):
                choices.append("Пока не знаю")
            if "Позвать менеджера" not in choices:
                choices.append("Позвать менеджера")
            if dialog.get("clarification_stalled") == missing:
                dialog["messages"].pop()
                self._route(state, dialog, "Не удалось уточнить запрос покупателя", "лицензии")
                self._decision(dialog, decision["intent"], "handoff", mandatory=True)
            else:
                dialog["clarification_stalled"] = list(missing)
        else:
            dialog.pop("clarification_stalled", None)

    def _answer_request(self, db, state, dialog, text, model=None, page_context=None, reference=None):
        folded = text.casefold()
        percent = re.search(r"(\d+(?:[.,]\d+)?)\s*%", folded)
        direct = re.search(r"(?:позов|позва|позви|подключ|соедин|переключ|переда|хочу|нужен|можно|дайте|зовите).{0,40}"
                           r"(?:человек|оператор|менеджер|специалист)", folded)
        if re.search(r"\b(?:нов(?:ый|ое|ая)\s+(?:софт|по|программ)|подобрать\s+(?:софт|программу))", folded) and not re.search(r"цен|стои", folded):
            dialog["guided_selection"] = True
            self._message(dialog, "assistant", "Какую задачу вы хотите решить с помощью программы?", mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["task"])
        if direct or re.fullmatch(r"\s*(?:оператор[ау]?|менеджер[ау]?|человек[ау]?)[.!?\s]*", folded):
            self._route(state, dialog, "Запрос покупателя", "лицензии")
            return self._decision(dialog, "operator", "handoff", mandatory=True)
        if reference and reference[0] in ("order", "document", "license"):
            return self._answer_account_reference(dialog, *reference)
        pending = dialog.pop("discount_counteroffer", None)
        if pending:
            reply = folded.strip(" .!?")
            if re.fullmatch(r"(нет|не подходит|не устраивает|отказываюсь|хочу больше)([, ].*)?", reply):
                self._route(state, dialog, "Покупателю не подходит скидка 5%", "скидка")
                return self._decision(dialog, "discount", "handoff", mandatory=True)
            if reply in {"да", "подходит", "согласен", "согласна", "да, подходит", "устраивает"}:
                folded = "скидка 5%"
                percent = re.search(r"(5)%", folded)
        facets = dialog.setdefault("facets", {})
        incoming, skipped_reply = self._incoming_facets(text, dialog)
        if page_context == OFFICE_BUSINESS_PAGE and not (incoming.get("product") or incoming.get("article")):
            incoming = {**facets_for(OFFICE_BUSINESS_NAME), **incoming}
        if skipped_reply:
            dialog.setdefault("optional_answers", {})[skipped_reply] = True
        if self._merge_facets(facets, incoming):
            dialog.pop("optional_answers", None)
            dialog.pop("viewed_offer", None)
        if re.search(r"\b(?:бронь|забронировать|бронирование|резервирование)\b", folded):
            self._route(state, dialog, "Бронирование по запросу покупателя", "заказы")
            return self._decision(dialog, "reservation", "handoff", mandatory=True)
        if (dialog.get("viewed_offer") and not page_context
                and not any(incoming.get(k) for k in ("products", "product", "article", "articles"))
                and self._viewed_minimum(db, state, dialog, folded)):
            return
        intent = classify(text)
        if not intent and dialog.get("decision", {}).get("intent") == "request_status":
            number = re.fullmatch(r"\s*(?:[a-zа-я]+[-_]\d+|\d{3,})\s*[.!]?\s*", folded)
            if number:
                facets["request_number"] = number[0].strip(" .!").upper()
                intent = "request_status"
        if demo_account.license_lookup(text, dialog):
            if is_guest(state):
                return self._login_prompt(dialog)
            answer, action, source = demo_account.license_answer(state, text)
            if action == "handoff":
                self._route(state, dialog, "Проверить покупку и выдачу ключа", "лицензии", notification=answer)
                if dialog["status"] == "queued":
                    dialog["messages"][-1]["text"] = answer + " Обращение в очереди."
                dialog["messages"][-1]["synthetic"] = True
            else:
                self._message(dialog, "assistant", answer, mode="rules", source=source, synthetic=action == "lookup")
            return self._decision(dialog, "license_lookup", action, evidence=[source] if source else [],
                                  mandatory=action == "handoff")
        facts = answers_from_knowledge(text)
        edi_requested = asks_edi_status(text)
        if is_guest(state) and personal_request(text) and not edi_requested:
            return self._login_prompt(dialog)
        if edi_requested or demo_account.contract_lookup(text) or (page_context == OFFICE_BUSINESS_PAGE and VIEWED_DETAILS.search(text) and not intent):
            evidence = []
            if VIEWED_DETAILS.search(text):
                offers = self._conversation_offers(db, state, text, facets, 2)
                if len(offers) == 1:
                    offer = offers[0]
                    price = f"{offer['price']:,.2f}".replace(",", " ").replace(".", ",").removesuffix(",00")
                    currency = "₽" if offer["currency"] in {"RUR", "RUB"} else offer["currency"]
                    detail = f"{price} {currency}" if offer["price"] > 0 else "цена по запросу"
                    self._message(dialog, "assistant", f"{offer['name']}: {detail}.", offers=offers,
                                  source=offer.get("url") or self._feed(db, state)["source"], mode="rules")
                    evidence.append(offer["id"])
                else:
                    self._message(dialog, "assistant", "Какую программу нужно подобрать и для какой задачи?", mode="rules")
            if edi_requested or demo_account.contract_lookup(text):
                for answer, source, _ in answers_from_knowledge(re.sub(r"\bэдо\b", "", text, flags=re.I)):
                    self._message(dialog, "assistant", answer, source=source, mode="rules")
                return self._company_profile(state, dialog, text, evidence)
            return self._decision(dialog, "selection", "inform" if evidence else "clarify",
                                  evidence=evidence, missing=[] if evidence else ["article"])
        basket = basket_request(catalogue_text(text), page_context, incoming)
        if basket:
            return self._answer_basket(db, state, dialog, page_context, basket, facts)
        if not dialog.get("viewed_offer") or page_context:
            topics = power_bi_purchase_topics(catalogue_text(text), incoming, page_context)
            if topics:
                return self._power_bi_purchase(state, dialog, facts, topics)
        if intent:
            for answer, source, _ in facts:
                self._message(dialog, "assistant", answer, source=source, mode="rules")
            return self._purchase_question(db, state, dialog, intent)
        order_number = re.search(r"\bSL-\d+\b", text, re.I)
        if order_number and re.search(r"заказ|статус|оплат|ключ", folded):
            order = state["orders"].get(order_number[0].upper())
            if order and self._can_view("buyer", order, state):
                answer = f"Заказ {order['id']}: {order['status']}. {order['license_status']}."
                self._message(dialog, "assistant", answer, source=order["source"], mode="rules")
                return self._decision(dialog, "post_purchase", "lookup", evidence=[order["id"]])
        hesitant = re.search(r"слишком дорого|дороговато|сомневаюсь.{0,25}(цен|покуп)|дорого.{0,20}подумаю", folded)
        if hesitant and "скид" not in folded:
            self._message(dialog, "assistant", "Проверить, доступна ли скидка до 5%?", choices=["Проверить скидку"], mode="rules")
            return self._decision(dialog, "discount", "clarify")
        if "скид" in folded:
            if re.search(r"не (?:нужна|хочу|надо).{0,12}скид|без скид|скидк\w* не (?:нужна|надо)", folded):
                dialog.pop("approved_discount", None)
                self._message(dialog, "assistant", "Оставляю цену каталога без дополнительной скидки.")
                return self._decision(dialog, "discount", "inform")
            recent = next((m["offers"] for m in reversed(dialog["messages"]) if m.get("offers")), [])
            if percent and float(percent[1].replace(",", ".")) > 5:
                discount_part = re.search(r"(?:дадите ли |можно ли |дадите |можно )?скидк[^?.!]*", text, re.I)
                if facets.get("product") and re.search(r"цен|стои", folded):
                    self._selection(db, state, dialog, text)
                    if discount_part and discount_part.start():
                        dialog["messages"][-1]["review_fragment"] = text[:discount_part.start()].rstrip(" ,и?")
                    price_message = dialog["messages"][-1]
                    recent = price_message.get("offers", [])
                    if len(recent) == 1 and recent[0]["available"] and recent[0]["price"] > 0:
                        offer = recent[0]
                        price_message["text"] = f"{offer['name']}: {offer['price']:g} {offer['currency']}."
                        price_message.pop("template_id", None)
                    elif len(recent) > 1:
                        price_message["text"] = "Какую редакцию выбрать для расчёта цены?"
                        price_message.pop("template_id", None)
                    if dialog.get("decision", {}).get("next_action") == "handoff":
                        return
                if len(recent) == 1 and self._reduced_price(recent[0]):
                    self._message(dialog, "assistant", "На эту позицию уже действует скидка. Скидки не суммируются.")
                    return self._decision(dialog, "discount", "inform")
                dialog["discount_counteroffer"] = True
                self._message(dialog, "assistant", "Могу предложить 5% для позиции без действующей скидки. Вам подходит?",
                              choices=["Да, подходит", "Не подходит"], mode="rules")
                if discount_part:
                    dialog["messages"][-1]["review_fragment"] = discount_part[0]
                return self._decision(dialog, "discount", "clarify", missing=["discount_acceptance"])
            if len(recent) == 1 and recent[0]["available"]:
                offer = recent[0]
                if self._reduced_price(offer) or re.search(r"купон|акци|корпоративн.{0,15}цен", folded):
                    self._message(dialog, "assistant", "Скидки не суммируются. Для этой позиции сохраняется действующая цена.")
                    return self._decision(dialog, "discount", "inform")
                rate = float(percent[1].replace(",", ".")) if percent else 5
                dialog["approved_discount"] = dict(offer_id=offer["id"], rate=rate)
                self._message(dialog, "assistant", f"Для позиции «{offer['name']}» согласована скидка {rate:g}%. Учту её при оформлении.", offers=[offer])
            else:
                self._message(dialog, "assistant", "По какой позиции проверить скидку? Откройте карточку и выберите «Расскажите о товаре» в новом чате.")
            return self._decision(dialog, "discount", "confirm_offer")
        if facts:
            for answer, source, intent in facts:
                self._message(dialog, "assistant", answer, source=source, mode="rules")
            sources = list(dict.fromkeys(source for _, source, _ in facts))
            needs_order_check = re.search(
                r'(?:не приш|не получ|не отображ|не виж|почему|когда).{0,80}(?:ключ|дистрибутив|сч[её]т)|'
                r'(?:ключ|дистрибутив|сч[её]т).{0,80}(?:не приш|не получ|не отображ|не виж)', folded)
            if needs_order_check:
                self._route(state, dialog, 'Проверка исполнения заказа', 'лицензии')
                return self._decision(dialog, 'post_purchase', 'handoff', evidence=sources, mandatory=True)
            if all(intent == 'checkout' for _, _, intent in facts) and (
                dialog.get('viewed_offer') or incoming.get('product') or incoming.get('article')
            ):
                return self._selection(db, state, dialog, text)
            clarify = [intent for _, _, intent in facts if intent in {'tax', 'renewal'}]
            return self._decision(dialog, 'knowledge' if len(facts) > 1 else facts[0][2],
                                  'clarify' if clarify else 'inform', evidence=sources,
                                  missing=['article'] if 'tax' in clarify else ['license'] if clarify else [])
        if any(word in folded for word in ("закрывающ", "где ключ", "где счёт", "статус заказ", "оплатили")):
            self._message(dialog, "assistant",
                          "Откройте «Мои заказы» в личном кабинете и выберите покупку. Статус, документы и доступные лицензии указаны в карточке заказа.",
                          source=KNOWLEDGE, mode="rules")
            return self._decision(dialog, "post_purchase", "inform", evidence=[KNOWLEDGE])
        if dialog.get("viewed_group") and not page_context and self._answer_product_group(db, state, dialog, text, incoming):
            return
        if any(term in folded for term in ("вне каталог", "нет в каталог", "нет на сайт", "запросить цен", "запрос цены", "запрашиваю цену", "нужна цена", "цена по запросу")):
            self._route(state, dialog, "Нужна проверка товара или цены", "лицензии")
            return self._decision(dialog, "catalog", "handoff", mandatory=True)
        learned = state.get("support_answers", {}).get(sales_chat.support_topic(text))
        if learned:
            self._message(dialog, "assistant", learned["text"], source="Ответ службы поддержки", mode="rules")
            return self._decision(dialog, "knowledge", "inform")
        if VIEWED_DETAILS.search(text):
            if incoming.get("articles"):
                self._message(dialog, "assistant", "Вы указали несколько артикулов. Какую позицию проверить первой?", mode="rules")
                return self._decision(dialog, "selection", "clarify", missing=["article"])
            if incoming.get("article"):
                return self._answer_article(db, state, dialog, text, facets)
            offer = self._viewed_offer(db, state, dialog, facets)
            if offer:
                source = self._feed(db, state)["source"]
                if not self._offer_term_matches(offer, text, facets):
                    self._message(dialog, "assistant", "На какой срок нужна лицензия? Менеджер проверит цену подходящего варианта.",
                                  source=source, mode="rules")
                    return self._decision(dialog, "selection", "clarify", missing=["article"])
                return self._answer_viewed_offer(db, state, dialog, offer, text)
        if reference and reference[0] == "offer" and dialog.get("viewed_offer") == reference[1]["id"]:
            return self._answer_viewed_offer(db, state, dialog, reference[1], text)
        # Consequential routes require explicit language confirmed above. The
        # model may refine a non-binding follow-up for purchase-related context.
        if model and model.get("decision_version") and not model.get("needs_clarification", True):
            if model.get("decision_intent") == "post_purchase" and re.search(
                r"заказ|купил|куплен|оплат|после покупк|получен", folded
            ):
                self._message(dialog, "assistant",
                              "Какой вопрос по покупке нужно решить: статус заказа, документы или ключ лицензии?",
                              source=KNOWLEDGE, mode="rules")
                return self._decision(dialog, "post_purchase", "clarify", missing=["order_question"])
        return self._selection(db, state, dialog, text)

    def _answer_viewed_offer(self, db, state, dialog, offer, text=""):
        source = self._feed(db, state)["source"]
        answer = ""
        asks_price = re.search(r"\b(?:цен[ауые]|сколько сто|стоимост)", text, re.I)
        if asks_price:
            answer = (f"Цена: {offer['price']:g} {offer['currency']}."
                      if offer["available"] else "Цена по запросу.")
        elif re.search(r"\b(?:наличи\w*|доступн\w*|доступен|в наличии)", text, re.I):
            answer = ("Товар доступен к заказу." if offer["available"] else
                      "Менеджер уточнит, можно ли заказать товар.")
        self._message(dialog, "assistant", answer, offers=[offer], source=source, mode="rules",
                      show_offer_details=not asks_price)
        if not offer["available"]:
            reason = "Подтверждение поставки" if offer["price"] > 0 else "Уточнение цены"
            self._route(state, dialog, reason, "лицензии")
            return self._decision(dialog, "catalog", "handoff", evidence=[offer["id"]], mandatory=True)
        return self._decision(dialog, "selection", "inform", evidence=[offer["id"]])

    def _answer_article(self, db, state, dialog, text, facets):
        offers = self._compatible_offers(db, state, facets.get("product"), facets, limit=2)
        source = self._feed(db, state)["source"]
        if len(offers) != 1 or not self._offer_term_matches(offers[0], text, facets):
            prompt = ("По артикулу найдено несколько позиций. Пришлите код нужной позиции из карточки."
                      if len(offers) > 1 else "По артикулу нет совпадения с вашими условиями. Проверьте код в карточке товара.")
            self._message(dialog, "assistant", prompt, source=source, mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["article"])
        offer = offers[0]
        if not offer["available"]:
            self._message(dialog, "assistant", f"{offer['name']}. Цена по запросу.", offers=[offer], source=source, mode="rules")
            self._route(state, dialog, "Проверка цены и возможности покупки по артикулу", "лицензии")
            return self._decision(dialog, "catalog", "handoff", evidence=[offer["id"]], mandatory=True)
        answer = f"{offer['name']}.\nЦена: {offer['price']:g} {offer['currency']}."
        if re.search(AVAILABILITY_DETAILS, text, re.I):
            answer += " По данным каталога, позиция доступна для оформления."
        self._message(dialog, "assistant", answer, offers=[offer], source=source, mode="rules")
        return self._decision(dialog, "selection", "inform", evidence=[offer["id"]])

    @staticmethod
    def _incoming_facets(text, dialog):
        incoming = facets_for(text)
        decision = dialog.get("decision", {})
        facets = dialog.get("facets", {})
        pending = next(iter(decision.get("missing", [])), None)
        if pending is None and decision.get("next_action") == "shortlist":
            # Older stored turns may have shown a model question without recording its field.
            messages = dialog.get("messages", [])[-2:]
            previous = messages[-1] if messages else {}
            if previous.get("role") == "buyer":
                previous = messages[0] if len(messages) == 2 else {}
            if previous.get("role") == "assistant" and (previous.get("model") or {}).get("published"):
                question = previous["text"].split("\n", 1)[0]
                pending = next((FOLLOW_UP_FIELDS[key] for key, value in FOLLOW_UP_TEXT.items() if value == question), None)
        if pending in {"platform", "infrastructure", "platforms"} or facets.get("topic") == "security":
            platform = next((name for name, pattern in PLATFORMS.items() if re.fullmatch(pattern, text.casefold().strip())), None)
            if platform:
                incoming.pop("product", None)
                incoming.pop("products", None)
                incoming["platforms"] = [platform]
        expecting_count = pending == "seats" or dialog.get("optional_answers", {}).get("seats") and not facets.get("seats")
        count = re.fullmatch(r"\s*(\d{1,6})\s*[.!]?\s*", text) if expecting_count else None
        if count and int(count[1]) > 0:
            incoming["seats"] = int(count[1])
        skipped = pending if re.fullmatch(r"\s*(?:гибко|не знаю|неважно|пока не знаю|без разницы)\s*[.!]?\s*", text.casefold()) else None
        return incoming, skipped

    def _power_bi_purchase(self, state, dialog, facts, topics):
        sources = []
        for answer, source, intent in facts:
            if intent != "checkout":
                self._message(dialog, "assistant", answer, source=source, mode="rules")
                sources.append(source)
        if "license" in topics:
            self._message(dialog, "assistant", "Softline сообщает цену Microsoft Power BI по запросу. "
                          "Менеджер проверит доступность лицензии и условия покупки.", source=POWER_BI_SOURCE, mode="rules")
            sources.append(POWER_BI_SOURCE)
        if "visuals" in topics:
            self._message(dialog, "assistant", "Менеджер уточнит, можно ли купить платные визуализации для Power BI "
                          "через Softline и на каких условиях.", mode="rules")
        reason = "Microsoft Power BI: " + " и ".join(topic for key, topic in (
            ("license", "цена и доступность лицензии"), ("visuals", "покупка визуализаций"),
        ) if key in topics)
        self._route(state, dialog, reason, "лицензии")
        return self._decision(dialog, "catalog", "handoff", evidence=list(dict.fromkeys(sources)), mandatory=True)

    def _login_prompt(self, dialog):
        self._message(dialog, "assistant", "Войдите в личный кабинет, чтобы продолжить с данными вашей покупки.", mode="rules")
        return self._decision(dialog, "account", "login")

    def _company_profile(self, state, dialog, text, evidence):
        if is_guest(state):
            return self._login_prompt(dialog)
        buyer = customer_id(state)
        if buyer not in demo_account.CONTRACTS:
            self._message(dialog, "assistant", "Для проверки организации войдите в профиль компании.", mode="rules")
            return self._decision(dialog, "account", "inform")
        parts, missing = [], []
        if demo_account.contract_lookup(text):
            active = state.get("contract_by_buyer", demo_account.CONTRACTS).get(buyer)
            if isinstance(active, bool):
                parts.append("У вашей организации есть действующий договор." if active else "У вашей организации нет действующего договора.")
            else:
                missing.append("договор")
        if asks_edi_status(text):
            status = state.get("edi_by_buyer", {"buyer1": True, "buyer2": False}).get(buyer)
            if isinstance(status, bool):
                parts.append("У вашей организации ЭДО подключён." if status else "У вашей организации ЭДО не подключён.")
            else:
                missing.append("подключение ЭДО")
        if parts:
            self._message(dialog, "assistant", " ".join(parts), mode="rules", synthetic=True)
            evidence.append("buyer_profile")
        if missing:
            subject = " и ".join(missing)
            self._route(state, dialog, "Проверить " + subject, "заказы",
                        notification=f"Менеджер проверит {subject} вашей организации.")
        return self._decision(dialog, "company_profile", "handoff" if missing else "lookup",
                              evidence=evidence, mandatory=bool(missing))

    def _answer_basket(self, db, state, dialog, context, request, facts):
        evidence = []
        documents = request["documents"]
        if documents and is_guest(state):
            return self._login_prompt(dialog)
        for answer, source, intent in facts:
            if intent != "tax":
                self._message(dialog, "assistant", answer, source=source, mode="rules")
                evidence.append(source)
        items = context.get("items")
        if items is None:
            if request["single"]:
                prompt = "Укажите артикул нужной позиции."
                missing = ["article"]
            elif request["tax"] and not (request["price"] or documents):
                prompt = "Укажите артикулы позиций, по которым нужно проверить НДС."
                missing = ["basket_items"]
            else:
                prompt = "Укажите артикулы или названия товаров и их количество."
                missing = ["basket_items", "quantity"]
            self._message(dialog, "assistant", prompt, mode="rules")
            return self._decision(dialog, "basket", "clarify", missing=missing, evidence=evidence)
        if not items:
            self._message(dialog, "assistant", "Добавьте нужные товары в корзину.", mode="rules")
            return self._decision(dialog, "basket", "clarify", missing=["basket_items"], evidence=evidence)
        if request["single"] and len(items) > 1:
            selected = context.get("selected_offer_id")
            if not selected:
                self._message(dialog, "assistant", "Какую позицию из корзины вы имеете в виду?", mode="rules")
                return self._decision(dialog, "basket", "clarify", missing=["basket_selection"], evidence=evidence)
            items = [line for line in items if line["offer_id"] == selected]

        priced, known, unknown, unavailable, out_of_bounds = [], [], [], [], []
        confirm_delivery = []
        for item in items:
            sku, quantity = item["offer_id"], item["quantity"]
            stored = db.execute("SELECT body FROM offers WHERE id=?", (sku,)).fetchone()
            if not stored:
                unknown.append(sku)
                continue
            offer = self._offer(stored[0], state)
            known.append(sku)
            minimum, maximum = offer.get("min_quantity", 1), offer.get("max_quantity", 100000)
            if not minimum <= quantity <= maximum:
                out_of_bounds.append(f"{offer['name']}: от {minimum} до {maximum} шт.")
            elif offer["price"] <= 0:
                unavailable.append(offer["name"])
            else:
                if not offer["available"]:
                    confirm_delivery.append(offer["name"])
                priced.append(self._document_line(offer, quantity))
        missing = []
        if unknown:
            self._message(dialog, "assistant", "Проверьте артикулы позиций, которых нет в текущем каталоге: "
                          + ", ".join(unknown) + ".", mode="rules")
            missing.append("article")
        evidence.extend(known)
        if out_of_bounds and (request["price"] or documents):
            self._message(dialog, "assistant", "Уточните количество для этих позиций:\n" + "\n".join(out_of_bounds), mode="rules")
            missing.append("quantity")
        if unavailable and (request["price"] or documents):
            self._message(dialog, "assistant", "Цена и наличие требуют подтверждения по позициям: "
                          + ", ".join(unavailable) + ".", mode="rules")
        currencies = {line["currency"] for line in priced}
        can_create = not (missing or unavailable) and len(currencies) == 1
        if can_create:
            for action in documents:
                doc = self._save_document(state, action, priced)
                title = "Готов учебный счёт" if action == "invoice" else "Готово учебное КП"
                self._message(dialog, "assistant", f"{title} {doc['id']}. Его можно скачать в разделе «Счета и платежи».",
                              document_id=doc["id"], mode="rules")
        if priced and (request["price"] or documents and not can_create):
            lines = [f"{line['name']}, {line['quantity']} шт. Стоимость {line['total']:.2f} {line['currency']}." for line in priced]
            if not (missing or unavailable) and len(currencies) == 1:
                total = sum(Decimal(str(line["total"])) for line in priced)
                lines.append(f"Итого по каталогу {total:.2f} {priced[0]['currency']}.")
            self._message(dialog, "assistant", "\n".join(lines), source=self._feed(db, state)["source"], mode="rules")
        if request["tax"] and known:
            self._message(dialog, "assistant", "По известным позициям НДС нужно подтвердить у менеджера.", mode="rules")
        if missing:
            return self._decision(dialog, "basket", "clarify", missing=missing, evidence=evidence)
        mixed_currency_document = documents and len(currencies) > 1
        if confirm_delivery:
            self._message(dialog, "assistant", "Менеджер подтвердит поставку: " + ", ".join(confirm_delivery) + ".")
        if request["tax"] or unavailable or mixed_currency_document or confirm_delivery:
            reason = "Проверка НДС по позициям корзины" if request["tax"] else "Проверка условий для позиций корзины"
            self._route(state, dialog, reason, "заказы")
            return self._decision(dialog, "basket", "handoff", evidence=evidence, mandatory=True)
        return self._decision(dialog, "basket", "document" if documents else "inform", evidence=evidence)

    @staticmethod
    def _merge_facets(previous, current):
        old_product = previous.get("product", "").casefold()
        new_product = current.get("product", "").casefold()
        same_mdaemon = {old_product, new_product} == {"mdaemon", "mdaemon messaging server"}
        refines_vendor = old_product in {"kaspersky", "криптопро"} and new_product.startswith(old_product + " ")
        switched = bool(old_product and new_product and new_product != old_product and not (same_mdaemon or refines_vendor))
        if switched:
            for name in ("products", "versions", "edition", "oem", "delivery", "article", "articles", "seats", "seat_range",
                         "quantity", "cores", "duration", "billing_period", "license_term", "license_operation",
                         "accessory", "device_type", "platforms", "architecture", "deployment", "topic", "targets",
                         "infrastructure", "purpose", "budget", "budget_limit", "value_preference"):
                previous.pop(name, None)
        if "seat_range" in current:
            previous.pop("seats", None)
        elif "seats" in current:
            previous.pop("seat_range", None)
        if "articles" in current:
            previous.pop("article", None)
        elif "article" in current:
            previous.pop("articles", None)
        previous.update(current)
        return switched

    def _purchase_question(self, db, state, dialog, intent):
        facets = dialog["facets"]
        if intent == "request_status":
            if not facets.get("request_number"):
                self._message(dialog, "assistant", "Подскажите номер заявки или обращения. По нему менеджер сможет проверить статус.", mode="rules")
                return self._decision(dialog, intent, "clarify", missing=["request_number"])
            self._route(state, dialog, "Проверка статуса обращения", "заказы")
            return self._decision(dialog, intent, "handoff", mandatory=True)
        if intent == "support_terms":
            self._message(dialog, "assistant",
                          "Уточните редакцию продукта и план поддержки, указанный в лицензии или договоре. По ним можно проверить состав услуг.",
                          mode="rules")
            return self._decision(dialog, intent, "clarify", missing=["support_plan"])
        if intent == "compatibility":
            missing = ["platforms", "concurrent_use"]
            text = "На каких устройствах нужно использовать продукт и сколько установок или одновременных подключений требуется?"
            if not facets.get("edition"):
                missing.insert(0, "edition")
                text = "Уточните редакцию. " + text
            self._message(dialog, "assistant", text, mode="rules")
            return self._decision(dialog, intent, "clarify", missing=missing)
        if intent == "mail_subscription":
            self._message(dialog, "assistant",
                          "Почта нужна на личном адресе или на домене организации? Для подбора тарифа укажите число пользователей.",
                          mode="rules")
            return self._decision(dialog, intent, "clarify", missing=["mail_account_type", "seats"])
        if intent == "registry":
            self._message(dialog, "assistant",
                          "Подтвердите точный артикул или номер записи. Наличие товара в реестре нужно сверить по официальной записи модели.",
                          mode="rules")
            return self._decision(dialog, intent, "clarify", missing=["registry_record_or_article"])
        if intent == "document":
            if facets.get("buyer_type") == "individual" or facets.get("device_type") == "mobile":
                self._route(state, dialog, "Проверка позиции и условий оплаты", "заказы")
                return self._decision(dialog, intent, "handoff", mandatory=True)
            if facets.get("buyer_type") == "education":
                text = "Сколько лицензий нужно школе? Укажите артикул или модель лицензии. Менеджер сможет проверить условия для образовательной организации."
                missing = ["article", "education_terms"]
            elif facets.get("product"):
                text = "Для счёта или КП подтвердите артикул и количество. Реквизиты передавайте менеджеру по согласованному каналу."
                missing = ["article", "billing_channel"]
            else:
                text = ("Назовите продукт, редакцию, операционную систему и количество. Если артикул известен, "
                        "пришлите его. Реквизиты передавайте менеджеру по согласованному каналу.")
                missing = ["product", "edition", "platform", "billing_channel"]
            if not facets.get("quantity") and not facets.get("seats"):
                missing.append("quantity")
            self._message(dialog, "assistant", text, mode="rules")
            return self._decision(dialog, intent, "clarify", missing=missing)
        route = {
            "special_terms": ("Особые условия закупки", "тендер"),
            "supplier": ("Проверка поставщика и условий договора", "тендер"),
            "migration_discount": ("Проверка права на скидку за переход", "скидка"),
            "promotion_terms": ("Проверка права на акцию или индивидуальную скидку", "скидка"),
            "technical_requirements": ("Проверка технического задания и комплектации", "тендер"),
            "resale_terms": ("Проверка партнёрского статуса и канала перепродажи", "тендер"),
            "contact": ("Запрос связи с менеджером", "заказы"),
        }[intent]
        self._route(state, dialog, *route)
        return self._decision(dialog, intent, "handoff", mandatory=True)

    def _selection(self, db, state, dialog, text):
        folded = text.casefold()
        facets = dialog.setdefault("facets", {})
        security = facets.get("topic") == "security" or any(term in folded for term in ("защит", "антивирус"))
        platform_reply = any(re.fullmatch(pattern, folded.strip()) for pattern in PLATFORMS.values()) and bool(facets.get("platforms"))
        query = facets.get("product") if platform_reply or facets.get("article") else self._query(text) or facets.get("product")
        security = security and (not query or "kaspersky" in query.casefold() or facets.get("topic") == "security")
        if facets.get("topic") == "office_suite" and (not query or "office" not in query.casefold()):
            self._message(dialog, "assistant",
                          "Какой офисный пакет нужен и на какой системе вы будете его использовать? Для проверки бессрочной лицензии нужна точная редакция.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["office_suite", "platform", "edition"])
        if len(facets.get("products", ())) > 1:
            if re.search(r"состав\w*\s+заказ|комплект|следующ\w+\s+позици", folded) and re.search(
                r"посчита|расчет|стоимост|срок\w*\s+поставк", catalogue_text(folded)
            ):
                self._route(state, dialog, "Проверка состава заказа, стоимости и сроков поставки", "лицензии")
                return self._decision(dialog, "selection", "handoff", mandatory=True)
            self._message(dialog, "assistant",
                          "Вы перечислили несколько продуктов. Какие нужно сравнить, а какие уже используются? Уточните требования к каждой позиции.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["products_to_compare", "requirements"])
        if query and query.casefold() == "csp" and not facets.get("article"):
            self._message(dialog, "assistant",
                          "Уточните производителя и редакцию криптопровайдера CSP. По ним проверю совместимость с вашей системой.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["publisher", "edition"])
        if query and query.casefold() == "криптопро" and facets.get("platforms") and not facets.get("article"):
            self._message(dialog, "assistant", "Какой продукт КриптоПро нужен, например CSP? Укажите его версию для проверки совместимости.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["product", "edition"])
        if query and query.casefold() == "kaspersky" and facets.get("device_type") == "desktop" and not facets.get("article"):
            self._message(dialog, "assistant",
                          "Нужна защита самих компьютеров? Уточните продукт Kaspersky и операционные системы на этих ПК.",
                          mode="rules")
            return self._decision(dialog, "security", "clarify", missing=["product", "platforms"])
        if facets.get("deployment") == "vdi":
            self._message(dialog, "assistant",
                          "Уточните платформу VDI и для каких задач нужна лицензия. Для учёбы и работы могут действовать разные условия.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["vdi_platform", "license_scope"])
        if facets.get("device_type") == "mobile" and not facets.get("architecture"):
            self._message(dialog, "assistant", "Какие модели планшетов и КПК используются? Для подбора нужна подтверждённая совместимость с их архитектурой.", mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["architecture", "device_models"])
        if facets.get("cores") and re.search(r"лиценз|метрик|ядер", folded):
            self._route(state, dialog, "Проверка метрики лицензирования на ядро", "лицензии")
            return self._decision(dialog, "selection", "handoff", mandatory=True)
        if security:
            facets["topic"] = "security"
            targets = set(facets.get("targets", ()))
            targets.update(name for match, name in (("почт", "email"), ("файл", "files"),
                           ("станци", "endpoints"), ("устройств", "endpoints"), ("компьютер", "endpoints"),
                           ("ноутбук", "endpoints"), ("сервер", "servers")) if match in folded)
            if re.search(r"\bпк\b", folded):
                targets.add("endpoints")
            facets["targets"] = sorted(targets)
            if re.search(r"windows|linux|macos|exchange|microsoft 365|m365|облач|локальн", folded):
                facets["infrastructure"] = text
            if re.search(r"бюджет|руб|₽", folded):
                facets["budget"] = text
            missing = [key for key in ("seats", "targets", "infrastructure", "budget")
                       if not facets.get(key) and key not in dialog.get("optional_answers", {})]
            if missing:
                prompts = {"seats": "Сколько сотрудников или устройств?",
                           "targets": "Что нужно защитить: почту, файлы или рабочие станции?",
                           "infrastructure": "Где работают почта и файлы? Какие операционные системы используют сотрудники?",
                           "budget": "Есть ли предел бюджета?"}
                prompt = prompts[missing[0]] if dialog.get("guided_selection") else " ".join(prompts[item] for item in missing)
                if dialog.get("guided_selection") and missing[0]=="targets" and (facets.get("seats") or facets.get("platforms")):
                    known = ", ".join(filter(None, (f"Устройств: {facets['seats']}" if facets.get("seats") else "",
                                                    ", ".join(facets.get("platforms", [])))))
                    prompt = f"{known}. Нужна защита рабочих станций, серверов или почты?"
                self._message(dialog, "assistant", prompt, mode="rules")
                return self._decision(dialog, "security", "clarify", missing=missing)
            query = "Kaspersky Endpoint Security"
        if not query and not facets.get("article"):
            if dialog.pop("search_by_name", False):
                matches = self._search(db, state, text)
                if matches:
                    self._message(dialog, "assistant", "Какая из найденных позиций вам подходит?",
                                  offers=matches, mode="rules")
                    return self._decision(dialog, "selection", "shortlist", evidence=[o["id"] for o in matches])
                self._message(dialog, "assistant", "По этому названию ничего не нашёл. Какую задачу нужно решить?", mode="rules")
                dialog["guided_selection"] = True
                return self._decision(dialog, "selection", "clarify", missing=["task"])
            if dialog.get("guided_selection"):
                if not facets.get("task"):
                    facets["task"] = text
                if not facets.get("seats") and not dialog.get("optional_answers", {}).get("seats"):
                    self._message(dialog, "assistant", "Сколько человек будут пользоваться программой?", mode="rules")
                    return self._decision(dialog, "selection", "clarify", missing=["seats"])
                if not facets.get("platforms") and not dialog.get("optional_answers", {}).get("platform"):
                    self._message(dialog, "assistant", "На каких устройствах будет работать программа?", mode="rules")
                    return self._decision(dialog, "selection", "clarify", missing=["platform"])
                subject = facets.get("task", "") + " " + text
                category = next((key for pattern, key in ((r"документ|таблиц|офис", "офис"),
                                                           (r"почт", "почт"), (r"график|чертеж", "график"))
                                 if re.search(pattern, subject, re.I)), "")
                options = self._search(db, state, category, 6) if category else []
                if options:
                    self._message(dialog, "assistant", "Вот позиции по вашей задаче. Условия каждой лицензии указаны в карточке.",
                                  offers=options, mode="rules")
                    return self._decision(dialog, "selection", "shortlist", evidence=[o["id"] for o in options])
                self._message(dialog, "assistant", "Для какой работы нужна программа: защита компьютеров, документы, почта или другая задача?", mode="rules")
                return self._decision(dialog, "selection", "clarify", missing=["category"])
            if not any(facets.values()) and not re.search(r"купить|подбор|подобрать|лицензи|программ|товар", folded):
                self._message(dialog, "assistant", "Можете уточнить, с каким вопросом вы обратились?", mode="rules")
                return self._decision(dialog, "unclear", "clarify", missing=["intent"])
            self._message(dialog, "assistant", "Укажите название продукта и на сколько пользователей или устройств нужна лицензия.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["product", "seats"])
        if query:
            facets["product"] = query
        quantity = facets.get("seats")
        offers = self._compatible_offers(db, state, query, facets)
        if not offers and query and query.casefold() == "криптопро csp" and facets.get("platforms"):
            self._message(dialog, "assistant",
                          "В каталоге не нашёл карточку CSP с подтверждённой совместимостью. Менеджер поможет проверить совместимость с вашей системой.",
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["compatible_article"])
        if not offers and query and query.casefold().startswith("kaspersky") and facets.get("device_type") == "desktop":
            missing = ["endpoint_article"]
            prompt = "В каталоге нет подтверждённого варианта для этих компьютеров. Какую задачу должна решать программа?"
            if not facets.get("platforms"):
                missing.append("platforms")
                prompt += " Какие операционные системы установлены на ПК?"
            self._message(dialog, "assistant", prompt, mode="rules")
            return self._decision(dialog, "security", "clarify", missing=missing)
        if not offers and facets.get("buyer_type") in {"budget", "education", "self_employed"}:
            prompts = {
                "budget": "Какие условия закупки действуют у вашей организации? Для какой задачи подбираете программу?",
                "education": "Сколько лицензий нужно образовательной организации?",
                "self_employed": "Для какой работы нужна программа? Менеджер проверит условия покупки для самозанятого.",
            }
            self._message(dialog, "assistant",
                          prompts[facets["buyer_type"]],
                          mode="rules")
            return self._decision(dialog, "selection", "clarify", missing=["eligible_offer"])
        if not offers:
            self._message(dialog, "assistant", "В каталоге нет точного совпадения. Можно изменить условия поиска или назвать другой продукт.", mode="rules")
            return self._decision(dialog, "selection", "refine_search")
        if not any(offer["available"] for offer in offers):
            self._message(dialog, "assistant", "Нашёл позиции в каталоге. Чтобы уточнить стоимость, выберите «Запросить цену» у нужной позиции.",
                          offers=offers, mode="rules")
            return self._decision(dialog, "selection", "shortlist", evidence=[item["id"] for item in offers])
        detail = ("Уточните редакцию и срок лицензии. Срок обновлений зависит от конкретной позиции."
                  if "mdaemon" in (query or "").casefold() else "Уточните редакцию, число устройств и срок лицензии.")
        if quantity:
            detail = f"Подборка для указанного количества мест: {quantity}. Выберите позицию, чтобы посмотреть условия."
        if security:
            detail = ("В каталоге есть варианты для рабочих станций. Для защиты почты и файлов "
                      "нужно отдельно проверить совместимость с вашей системой.")
        published = next((t for t in state["templates"].values()
                          if t["kind"] == "answer" and t["status"] == "published"), None)
        if published:
            detail = self._render(published, text, offers[0], extra={"answer": detail})
        self._message(dialog, "assistant", detail, offers=offers, source=self._feed(db, state)["source"], mode="rules",
                      template_id=published["id"] if published else None)
        return self._decision(dialog, "security" if security else "selection", "shortlist",
                              evidence=[item["id"] for item in offers])

    def _conversation_offers(self, db, state, text, facets, limit):
        if len(facets.get("products", ())) > 1:
            return []
        if facets.get("product") or facets.get("article"):
            return self._compatible_offers(db, state, facets.get("product"), facets, limit)
        query = self._query(text)
        return self._compatible_offers(db, state, query, {**facets, "product": query}, limit) if query else []

    def _viewed_minimum(self, db, state, dialog, text):
        pending = "minimum_criterion" in dialog.get("decision", {}).get("missing", [])
        asks_minimum = re.fullmatch(r"[\w\s,]*(?:минимальн\w*|базов\w*)\s+(?:верси\w*|редакци\w*|вариант\w*)[?!.\s]*", text)
        refers_back = re.fullmatch(r"\s*(?:(?:тут|здесь)\s+)?(?:только\s+)?один\s+(?:товар|продукт)|(?:он|она|товар)\s+в\s+начале\s+(?:переписки|чата)", text.strip(" .!?"))
        quantity = pending and re.fullmatch(r"(?:по\s+)?(?:количеств\w*|числ\w*)(?:\s+(?:ящик\w*|лицензи\w*|мест))?[.!?\s]*", text)
        features = pending and re.fullmatch(r"(?:по\s+)?(?:функци\w*|возможност\w*|редакци\w*)[.!?\s]*", text)
        if not (asks_minimum or refers_back or quantity or features):
            return False
        row = db.execute("SELECT body FROM offers WHERE id=?", (dialog["viewed_offer"],)).fetchone()
        if not row:
            return False
        offer = self._offer(row[0], state)
        facets = dialog["facets"]
        if facets.get("articles") or (facets.get("article") and str(facets["article"]).casefold() not in
                                     {offer["id"].casefold(), (offer.get("vendor_code") or "").casefold()}):
            return False
        # Older chats can still contain multiple families inferred from one catalogue title.
        if facets.get("products") and facets["products"] != facets_for(offer["name"]).get("products"):
            return False
        if features:
            self._route(state, dialog, "Сравнение возможностей редакций: " + offer["name"], "лицензии")
            self._decision(dialog, "selection", "handoff", evidence=[offer["id"]], mandatory=True)
            return True
        low, high = offer.get("min_quantity"), offer.get("max_quantity")
        detail = f"По этой позиции заказ от {low} до {high} шт." if low and high else (
            f"По этой позиции заказ от {low} шт." if low else "В карточке минимальное количество не указано.")
        clarify = bool(asks_minimum or (refers_back and pending))
        question = " Вы имеете в виду количество для заказа или набор функций?" if asks_minimum else ""
        lead = "Да, речь об одной позиции: " if refers_back else ""
        self._message(dialog, "assistant", f"{lead}«{offer['name']}». {detail}{question}",
                      source=offer.get("url") or self._feed(db, state)["source"], mode="rules")
        self._decision(dialog, "selection", "clarify" if clarify else "inform",
                       missing=["minimum_criterion"] if clarify else [], evidence=[offer["id"]])
        return True

    def _viewed_offer(self, db, state, dialog, facets):
        offer_id = dialog.get("viewed_offer")
        if not offer_id or len(facets.get("products", ())) > 1:
            return None
        row = db.execute("SELECT body FROM offers WHERE id=?", (offer_id,)).fetchone()
        if not row:
            return None
        offer = self._offer(row[0], state)
        article = facets.get("article")
        if article and article.casefold() not in {offer_id.casefold(), (offer.get("vendor_code") or "").casefold()}:
            return None
        return offer if self._matches_constraints(offer, facets) else None

    @staticmethod
    def _offer_term_matches(offer, text, facets):
        requested = license_months(text) or license_months(facets.get("duration", ""))
        if not requested:
            return True
        terms = [offer["name"], *offer.get("params", {}).get("Срок действия", ())]
        confirmed = set().union(*(license_months(term) for term in terms))
        return len(requested) == 1 and requested == confirmed

    def _compatible_offers(self, db, state, query, facets, limit=8):
        article = facets.get("article")
        family = (query or "").casefold()
        if not article and family in {"csp", "kaspersky", "криптопро"} and (
            family == "csp" or facets.get("device_type") or facets.get("platforms")
        ):
            return []
        if article:
            if family == "csp":
                facets = {**facets, "product": "криптопро csp"}
            elif family == "kaspersky" and facets.get("device_type") == "desktop":
                facets = {**facets, "product": "kaspersky endpoint security"}
            rows = db.execute("SELECT body FROM offers WHERE id=? COLLATE NOCASE OR lower(json_extract(body,'$.vendor_code'))=?",
                              (article, article))
            offers = [self._offer(row[0], state) for row in rows]
        else:
            seats = facets.get("seats")
            version = facets.get("versions", [])
            suffix = [str(seats)] if seats else []
            if len(version) == 1:
                suffix.append(version[0])
            alternatives = [query]
            if query == "криптопро csp":
                alternatives += ["крипто-про csp", "cryptopro csp", "криптопро ссп"]
            offers = {}
            for alternative in alternatives:
                if suffix:
                    for offer in self._search(db, state, " ".join([alternative, *suffix]), 64):
                        offers.setdefault(offer["id"], offer)
                for offer in self._search(db, state, alternative, max(limit, 220) if limit is not None else -1):
                    offers.setdefault(offer["id"], offer)
            offers = offers.values()
        return [offer for offer in offers if self._matches_constraints(offer, facets)][:limit]

    @staticmethod
    def _matches_constraints(offer, facets):
        name = offer["name"].casefold()
        params = offer.get("params", {})

        def attributes(*names):
            return " ".join(value.casefold() for label in names for value in params.get(label, ()))

        def matches(value, evidence):
            return re.search(rf"(?<![\w.]){re.escape(str(value))}(?![\w.])", evidence)

        versions = facets.get("versions", [])
        product = facets.get("product", "")
        if product and not Store._is_primary_product(offer, product):
            return False
        family = product.casefold()
        if family == "криптопро csp" or family.startswith("kaspersky endpoint security"):
            if not re.search(r"\b(?:софт|software|по)\b", attributes("Тип продукта")):
                return False
            if re.search(r"шлюз|\bngate\b|аппаратн|mail gateway|\bsecurity gateway\b", name):
                return False
            license_type = attributes("Тип лицензии")
            if not facets.get("accessory") and re.search(
                r"модул|дистрибутив|расширени|плагин|надстройк|\b(?:add-on|addon|plugin)\b", license_type + " " + name
            ):
                return False
            operation = facets.get("license_operation")
            if (operation != "renewal" and re.search(r"продлен|renewal", license_type)) or (
                operation != "upgrade" and re.search(r"обновлен|upgrade", license_type)
            ):
                return False
        if versions and not any(matches(version, name + " " + attributes("Версия")) for version in versions):
            return False
        if product in {"windows", "windows server"} and versions and not any(
            re.search(rf"(?<!\w){re.escape(product)}\s+(?:(?:standard|datacenter|pro)\s+)?{re.escape(version)}(?![\w.])", name)
            for version in versions
        ):
            return False
        editions = {"standard": ("standard", "стандартная", "стандартный"),
                    "pro": ("pro", "professional", "профессиональная"),
                    "home_business": ("home & business", "home and business", "для дома и бизнеса")}
        edition = facets.get("edition")
        if edition and not any(matches(value, name) for value in editions.get(edition, (edition,))):
            return False
        delivery = name + " " + attributes("Тип поставки", "Тип лицензии")
        box = re.search(r"коробоч|\b(?:box|fpp|retail)\b", delivery)
        oem = re.search(r"\boem\b", delivery)
        if facets.get("delivery") == "box" and not box:
            return False
        if facets.get("oem") == "required" and not oem:
            return False
        if facets.get("oem") == "excluded" and (oem or not box):
            return False
        platform_evidence = attributes("Совместимая архитектура/платформа", "Разрядность")
        architecture = name + " " + platform_evidence
        if facets.get("architecture") and not matches(facets["architecture"], architecture):
            return False
        if facets.get("device_type") == "mobile" and not re.search(r"android|ios|планшет|мобильн|\bкпк\b", architecture):
            return False
        if facets.get("device_type") == "desktop":
            audience = architecture + " " + attributes("Назначение", "Компоненты пакетов")
            if re.search(r"\b(?:mail|security) gateway\b|почтов\w* шлюз", name):
                return False
            if not re.search(DESKTOP + r"|\bwindows\s+(?:10|11)\b|\bmac\s?os\b", audience):
                return False
        for platform in facets.get("platforms", ()):
            match = PLATFORMS[platform]
            if re.search(rf"\b(?:без|не поддерживает)\s+(?:{match})\b", architecture):
                return False
            if not re.search(rf"\b(?:{match})\b", platform_evidence) and not re.search(
                rf"(?:\b(?:для|под|for|на)\s+(?:ос\s+)?|\()(?:{match})\b", name
            ):
                return False
        seats = facets.get("seats")
        quantity = facets.get("quantity")
        if quantity and not offer.get("min_quantity", 1) <= quantity <= offer.get("max_quantity", 100000):
            return False
        if seats:
            capacities = [int(v) for v in params.get("К-во пользователей до", []) if v.isdecimal()]
            if not (any(seats <= n for n in capacities) or matches(seats, attributes("К-во узлов"))
                    or re.search(rf"\b{seats}\s*(?:пользовател|устройств|мест|рабочих|сотрудник|компьютер|пк\b)", name)):
                return False
        if facets.get("buyer_type") == "education" and not re.search(
            r"образоват|учебн|academic|школ", attributes("Тип организации", "Пользователь", "Тип лицензии")
        ):
            return False
        if facets.get("buyer_type") == "budget" and not re.search(
            r"государствен|муниципальн|бюджетн|government|\bgov\b",
            attributes("Тип организации", "Пользователь", "Тип лицензии"),
        ):
            return False
        if facets.get("buyer_type") == "self_employed" and not re.search(
            r"самозанят", attributes("Тип организации", "Пользователь", "Тип лицензии"),
        ):
            return False
        if facets.get("organization_type") == "commercial" and not re.search(
            r"коммерческ|business", attributes("Тип организации") + " " + name,
        ):
            return False
        if facets.get("jurisdiction") != "foreign" and re.search(
            r"иностранн|foreign", name + " " + attributes("Тип организации", "Пользователь"),
        ):
            return False
        if not facets.get("accessory") and re.search(
            r"надстройк|дополнени|плагин|\b(?:add-on|addon|plugin)\b", attributes("Тип лицензии"),
        ):
            return False
        if facets.get("purpose") == "noncommercial" and not re.search(
            r"некоммерческ|домашн|noncommercial|\bhome\b",
            name + " " + attributes("Пользователь", "Тип лицензии"),
        ):
            return False
        if facets.get("license_term") == "perpetual" and not re.search(
            r"бессроч|perpetual", name + " " + attributes("Срок действия", "Тип лицензии"),
        ):
            return False
        return True

    @staticmethod
    def _is_primary_product(offer, product):
        name = " ".join(normalize_brand(offer["name"]).casefold().replace("ё", "е").split())
        product = " ".join(product.casefold().replace("ё", "е").split())
        vendor = " ".join(normalize_brand(offer.get("vendor", "")).casefold().split())
        publisher = vendor.strip(" «»\"")
        if product.startswith("kaspersky") and publisher not in {"kaspersky", "kaspersky lab", "лаборатория касперского"}:
            return False
        if product.startswith("криптопро") and publisher not in {"криптопро", "крипто-про", "cryptopro", "crypto pro"}:
            return False
        if product == "csp":
            return False
        words = [name]
        if vendor and name.startswith(vendor + " "):
            words.append(name[len(vendor):].strip(" -–—«\""))
        prefix = r"(?:(?:электронная\s+)?лицензия\s+(?:на\s+)?|программный продукт\s+)?"
        aliases = {
            "windows": ("windows", "microsoft windows"),
            "windows server": ("windows server", "microsoft windows server"),
            "office": ("office", "microsoft office"),
            "microsoft office": ("office", "microsoft office"),
            "ред ос": ("ред ос", "ред софт ред ос", "red soft ред ос"),
            "альт рабочая станция": ("альт рабочая станция", "basealt альт рабочая станция"),
            "альт сервер": ("альт сервер", "basealt альт сервер"),
            "криптопро": ("криптопро", "крипто-про", "cryptopro"),
            "криптопро csp": ("криптопро csp", "крипто-про csp", "cryptopro csp", "криптопро ссп"),
            "криптопро ngate": ("криптопро ngate", "крипто-про ngate", "cryptopro ngate"),
        }
        for candidate in aliases.get(product, (product,)):
            if any(re.match(prefix + re.escape(candidate) + r"(?!\w)", title)
                   or re.match(re.escape(search_key(candidate)) + r"(?!\w)", search_key(title)) for title in words):
                return True
        return False

    def _price_request(self, db, state, payload):
        offer_id = string(payload, "offer_id")
        row = db.execute("SELECT body FROM offers WHERE id=?", (offer_id,)).fetchone()
        if not row:
            raise KeyError(offer_id)
        offer = self._offer(row[0], state)
        dialog = self._dialog(state, payload, create=True)
        self._own(state, dialog)
        if dialog["status"] == "resolved":
            raise ValueError("Чат завершён")
        self._message(dialog, "buyer", f"Запрос цены на «{offer['name']}», код в каталоге {offer_id}.", price_card=True)
        dialog.setdefault("facets", {})["offer_id"] = offer_id
        self._route(state, dialog, "Подтверждение цены выбранной позиции", "лицензии")
        self._decision(dialog, "catalog", "handoff", mandatory=True, evidence=[offer_id])
        return {"dialog": dialog}

    @staticmethod
    def _query(text):
        product, _ = product_for(text)
        if product:
            return product
        folded = text.casefold()
        if any(word in folded for word in ("защит", "антивирус")):
            return "Kaspersky Endpoint Security"
        return "Kaspersky" if "касперск" in folded else ""

    @staticmethod
    def _own(state, item):
        if item.get("buyer_id", "buyer1") != customer_id(state):
            raise KeyError("Запись недоступна")

    def _document(self, db, state, action, payload):
        offer_id = string(payload, "offer_id")
        row = db.execute("SELECT body FROM offers WHERE id=?", (offer_id,)).fetchone()
        if not row:
            raise KeyError(offer_id)
        offer = self._offer(row[0], state)
        quantity = payload.get("quantity", 1)
        if type(quantity) is not int or not 1 <= quantity <= 100000:
            raise ValueError("Укажите целое количество от 1 до 100 000")
        if not offer.get("min_quantity", 1) <= quantity <= offer.get("max_quantity", 100000):
            raise ValueError("Количество вне диапазона выбранной позиции")
        if amount(payload.get("discount", 0)) != 0:
            raise ValueError("Скидку рассчитывает помощник")
        approved = {}
        if payload.get("dialog_id"):
            dialog = self._dialog(state, payload)
            self._own(state, dialog)
            approved = dialog.get("approved_discount", {})
        discount = amount(approved["rate"], 0, 5) if approved.get("offer_id")==offer_id else Decimal(0)
        if offer["price"] <= 0:
            dialog = self._dialog(state, payload, create=True)
            self._own(state, dialog)
            reason = "Нужно подтвердить наличие и цену"
            self._message(dialog, "buyer", f"{offer['name']}, количество {quantity}, скидка {discount}%.",
                          offer_id=offer_id)
            self._route(state, dialog, reason, "лицензии")
            self._decision(dialog, "catalog", "handoff",
                           mandatory=True, evidence=[offer_id])
            return {"dialog": dialog}
        price = amount(offer["price"])
        if "expected_price" in payload and amount(payload["expected_price"]) != price:
            raise ValueError("Цена изменилась. Снова выберите позицию в каталоге")
        doc = self._save_document(state, action, [self._document_line(offer, quantity, discount)])
        if not offer["available"]:
            dialog = self._dialog(state, payload, create=True)
            self._own(state, dialog)
            self._message(dialog, "assistant", f"Документ {doc['id']} готов. Поставку подтвердит менеджер.", document_id=doc["id"])
            self._route(state, dialog, "Подтверждение поставки " + offer["name"], "заказы")
            return {"dialog": dialog, "document": self._document_view(doc)}
        if payload.get("dialog_id"):
            dialog = self._dialog(state, payload)
            self._own(state, dialog)
            text = f"Документ {doc['id']} готов."
            if action == "proposal" and self._reduced_price(offer):
                text += " Хороший выбор: по каталогу цена этой позиции снижена."
            self._message(dialog, "assistant", text, document_id=doc["id"])
        return {"document": self._document_view(doc)}

    @staticmethod
    def _document_line(offer, quantity, discount=Decimal(0)):
        price = amount(offer["price"])
        subtotal = price * quantity
        total = (subtotal * (100 - discount) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line = dict(offer_id=offer["id"], name=offer["name"], unit_price=float(price), quantity=quantity,
                    discount=float(discount), subtotal=float(subtotal), total=float(total), currency=offer["currency"],
                    article=offer.get("vendor_code") or offer["id"], offer_url=offer["url"], tax=UNKNOWN)
        for key, param in (("payment", "Способ оплаты"), ("delivery", "Срок поставки"), ("validity", "Срок действия КП"),
                           ("license_term", "Срок действия"), ("delivery_type", "Тип поставки")):
            line[key] = " / ".join(v for v in offer.get("params", {}).get(param, []) if v.casefold() not in
                                   {"не определено", "другое или отсутствует"}) or UNKNOWN
        return line

    def _save_document(self, state, action, lines):
        first = lines[0]
        buyer_id = customer_id(state)
        if is_guest(state):
            raise PermissionError("Для оформления нужен вход.")
        doc = {**first, "id": identifier("INV" if action == "invoice" else "KP"), "kind": action,
               "buyer_id": buyer_id, "recipient": PROFILES[buyer_id], "supplier": "ООО «Демо-Поставщик»",
               "feed_day": state["day"], "created_at": now()}
        if len(lines) > 1:
            doc.update(lines=lines, name="Товары из корзины", offer_id="", offer_url="", unit_price=None,
                       article=", ".join(line["article"] for line in lines),
                       quantity=sum(line["quantity"] for line in lines),
                       subtotal=float(sum(Decimal(str(line["subtotal"])) for line in lines)),
                       total=float(sum(Decimal(str(line["total"])) for line in lines)))
            for key in ("payment", "delivery_type", "delivery", "license_term", "validity"):
                if any(line[key] != first[key] for line in lines):
                    doc[key] = UNKNOWN
        if action == "proposal":
            doc["validity"] = "30 дней с даты выставления"
        templates = [t for t in state["templates"].values() if t["status"] == "published"
                     and t["kind"] == ("invoice" if action == "invoice" else "quote")]
        if templates:
            template = templates[-1]
            values = {key: doc[key] for key in (
                "quantity", "discount", "subtotal", "total", "tax", "recipient", "payment", "delivery", "validity"
            )}
            values.update(document_id=doc["id"], date=printable_date(doc["created_at"]))
            doc["template_id"] = template["id"]
            offer = {"name": doc["name"], "id": doc["article"],
                     "price": doc["unit_price"] if doc["unit_price"] is not None else "См. позиции документа",
                     "currency": doc["currency"]}
            doc["template_text"] = self._render(template, offer=offer, extra=values)
        doc["rendered_html"] = render_document(doc)
        state["documents"][doc["id"]] = doc
        return doc

    @staticmethod
    def _document_view(doc):
        return {key: value for key, value in doc.items() if key != "rendered_html"}

    def _download(self, state, payload, role="buyer"):
        doc = state["documents"][string(payload, "document_id")]
        if role == "buyer":
            self._own(state, doc)
        content = doc.get("rendered_html") or render_document(doc)
        return dict(filename=f"{doc['id']}.html", content_type="text/html; charset=utf-8", content=content)

    @staticmethod
    def _classify(item, payload):
        for flag in ("manual_review", "collect_sample"):
            if flag in payload:
                if type(payload[flag]) is not bool:
                    raise ValueError("Укажите логическое значение признака")
                item["flags"][flag] = payload[flag]
        for field in ("label", "note"):
            if field in payload:
                item[field] = string(payload, field, optional=True, limit=1000)

    def _moderate(self, state, role, payload):
        dialog = self._dialog(state, payload)
        if not self._can_view(role, dialog):
            raise PermissionError("Диалог назначен другому менеджеру")
        self._classify(dialog, payload)
        review = dict(id=identifier("R"), dialog_id=dialog["id"], role=role, kind="moderation",
                      flags=dict(dialog["flags"]), note=dialog.get("note", ""),
                      label=dialog.get("label", ""), at=now())
        state["reviews"].append(review)
        return {"dialog": dialog, "review": review}

    def _sample_classify(self, state, role, payload, scope):
        sample_id = string(payload, "sample_id")
        source = self._visible_samples(scope)[sample_id]
        sample = state.setdefault("samples", {}).setdefault(
            sample_id, {**source, "flags": dict(source["flags"]), "facets": dict(source["facets"])}
        )
        self._classify(sample, payload)
        facets = sample.setdefault("facets", dict.fromkeys(SAMPLE_FACETS, ""))
        for field in SAMPLE_FACETS:
            if field in payload:
                facets[field] = string(payload, field, optional=True, limit=120)
        review = dict(id=identifier("R"), kind="classification", sample_id=sample_id, role=role,
                      flags=dict(sample["flags"]), facets=dict(facets), label=sample["label"],
                      note=sample["note"], at=now())
        state["reviews"].append(review)
        return {"sample": sample, "review": review}

    def _review(self, state, role, payload):
        item, _, _ = reviews.lookup(
            state, role, string(payload, "dialog_id"), string(payload, "message_id"),
            string(payload, "version", optional=True),
        )
        rating = payload.get("rating")
        if rating not in ("+", "-"):
            raise ValueError("Выберите + или −")
        review = {**item, "id": identifier("R"), "rating": rating, "role": role, "kind": "rating",
                  "note": reviews.redact(string(payload, "note", optional=True)), "at": now()}
        state["reviews"].append(review)
        return {"review": review}

    def _template(self, db, state, action, payload):
        template_id = string(payload, "template_id", optional=action == "template_draft")
        templates = state["templates"]
        if action == "template_draft":
            prev = templates[template_id] if template_id else None
            if prev and prev["status"] == "draft":
                template_id = prev["id"]
            else:
                template_id = identifier("T")
            kind = payload.get("kind", prev["kind"] if prev else "answer")
            if kind not in ("answer", "quote", "invoice"):
                raise ValueError("Укажите тип шаблона")
            templates[template_id] = dict(id=template_id, status="draft", kind=kind,
                name=string(payload, "name", optional=True, limit=120) or (prev["name"] if prev else "Шаблон"),
                text=string(payload, "text"), updated_at=now(), emulation=None)
        else:
            template = templates[template_id]
            if action == "template_emulate":
                query = string(payload, "text", optional=True)
                offers = self._search(db, state, self._query(query) or query, 3)
                offer = offers[0] if offers else None
                example = dict(answer="Уточните редакцию и срок лицензии.", quantity=1, discount=0,
                               tax=UNKNOWN, payment=UNKNOWN, validity=UNKNOWN,
                               document_id="DEMO-001", date=printable_date(now()),
                               recipient=PROFILES[customer_id(state)])
                if offer:
                    example.update(subtotal=offer["price"], total=offer["price"],
                                   delivery=" / ".join(offer.get("params", {}).get("Срок поставки", [])) or UNKNOWN)
                rendered = self._render(template, query, offer, example)
                template["emulation"] = {"text": rendered, "missing": sorted(set(re.findall(r"\{([a-z_]+)\}", rendered))),
                                         "question": query, "offers": offers, "at": now()}
            elif action == "template_publish":
                if template["status"] != "draft" or not template["emulation"]:
                    raise ValueError("Сначала проверьте черновик в эмуляции")
                for previous in templates.values():
                    if previous["kind"] == template["kind"] and previous["status"] == "published":
                        previous["status"] = "retired"
                template["status"] = "published"
            elif action == "template_rollback":
                if template["status"] != "retired":
                    raise ValueError("Выберите предыдущую публикацию")
                for previous in templates.values():
                    if previous["kind"] == template["kind"] and previous["status"] == "published":
                        previous["status"] = "retired"
                template["status"] = "published"
            elif action == "template_delete":
                if template["status"] != "draft":
                    raise ValueError("Удалить можно только черновик")
                templates.pop(template_id)
                return {"deleted": template_id}
            else:
                raise ValueError("Неизвестное действие")
        return {"template": templates[template_id]}
