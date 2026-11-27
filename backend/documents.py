"""Printable mock invoice and quote layout."""

from datetime import datetime
from html import escape
from urllib.parse import urlsplit


UNKNOWN = "Требует уточнения"
PROFILES = {
    "personal": "Марина Орлова",
    "buyer1": "ООО «Северный контур»",
    "buyer2": "ООО «Вектор систем»",
}

TEMPLATES = [
    ("T-standard-answer", "Ответ по карточке", "answer", "{answer}"),
    ("T-standard-quote", "Коммерческое предложение", "quote", "Если меняется количество или состав заказа, запросите новое предложение."),
    ("T-standard-invoice", "Счёт на оплату", "invoice", "Для уточнения состава счёта укажите менеджеру код {article}."),
]


def default_templates():
    return {identifier: dict(id=identifier, name=name, kind=kind, status="published", text=text,
                             updated_at="2026-11-26", emulation=None, builtin=True)
            for identifier, name, kind, text in TEMPLATES}


def printable_date(value):
    return datetime.fromisoformat(value).strftime("%d.%m.%Y")


def render_document(doc):
    def field(name, fallback=UNKNOWN):
        return escape(str(doc.get(name) or fallback))

    order = doc["kind"] == "invoice"
    title = "Счёт на оплату" if order else "Коммерческое предложение"
    number = field("id")
    created = printable_date(doc["created_at"])
    currency = field("currency")
    supplier = field("supplier", "ООО «Демо-Поставщик»")
    recipient = field("recipient", PROFILES["buyer1"])
    attachment = "" if doc.get("lines") and doc.get("template_id") == "T-standard-invoice" else field("template_text", "")
    bank = ("<div><dt>Реквизиты для оплаты</dt><dd>В учебном счёте платёжные реквизиты отсутствуют.</dd></div>"
            if order else "")
    conditions = [("Способ оплаты", "payment"), ("Тип поставки", "delivery_type"), ("Срок поставки", "delivery"),
                  ("Срок лицензии", "license_term")]
    if not order:
        conditions.append(("Срок действия КП", "validity"))
    supplied = "".join(f"<div><dt>{label}</dt><dd>{field(key)}</dd></div>" for label, key in conditions
                       if doc.get(key) and doc[key] != UNKNOWN)
    missing = [label[:1].lower() + label[1:] for label, key in conditions if not doc.get(key) or doc[key] == UNKNOWN]
    note_intro = "Для позиций нужно уточнить " if doc.get("lines") else "В карточке не указаны: "
    note = f"<p>{note_intro}{', '.join(missing)}.</p>" if missing else ""
    rows = "".join(f"""<tr><td data-label="Артикул">{escape(line.get('article') or line['offer_id'])}</td>
<td data-label="Товар">{escape(line['name'])}</td><td data-label="Количество">{line['quantity']}</td>
<td class="amount" data-label="Цена за единицу">{line['unit_price']:.2f} {currency}</td>
<td data-label="Скидка">{line['discount']:g}%</td><td class="amount" data-label="Итог">{line['total']:.2f} {currency}</td>
<td data-label="В т. ч. НДС">{escape(line.get('tax') or UNKNOWN)}</td></tr>""" for line in (doc.get("lines") or [doc]))
    url = doc.get("offer_url", "")
    card = (f'<p><a href="{escape(url, quote=True)}" rel="noreferrer">Открыть карточку товара</a></p>'
            if urlsplit(url).scheme in {"http", "https"} and urlsplit(url).hostname else "")
    instruction = ("Передайте номер счёта менеджеру для проверки условий и получения документа на оплату."
                   if order else "Передайте номер предложения менеджеру, чтобы согласовать условия и запросить счёт.")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} {number}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;padding:clamp(12px,4vw,48px);color:#201b1f;background:#f1efef;
font:16px/1.5 system-ui,sans-serif;overflow-wrap:anywhere}} main{{max-width:960px;margin:auto;background:white;
padding:clamp(16px,4vw,48px);border-top:5px solid #98153b}} header{{margin-bottom:24px}} h1,h2,p,dl{{margin:0}}
h1{{font-size:clamp(24px,4vw,36px);line-height:1.15}} h2{{font-size:20px;margin:22px 0 6px}}
.meta{{margin:8px 0 14px;color:#594b52}} .mark{{color:#6d1c38;font-weight:600}} .details{{display:grid;
grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}} dt{{color:#594b52;font-size:14px}}
dd{{margin:0;font-weight:600}} table{{border-collapse:collapse;width:100%;margin-top:16px}} th,td{{text-align:left;
padding:9px;border-bottom:1px solid #e1dbde;vertical-align:top}} th{{background:#f6eff2;overflow-wrap:normal}}
th:nth-child(3),th:nth-child(5){{white-space:nowrap}} .amount,tbody td:last-child{{overflow-wrap:normal}} td:before{{display:none}}
tfoot td{{font-weight:650}} a{{color:#8d1237}} .template{{white-space:pre-wrap;margin-top:10px}} footer{{margin-top:16px}}
@media(max-width:700px){{thead{{position:absolute;width:1px;height:1px;clip-path:inset(50%);overflow:hidden}} table, tbody, tfoot, tr, td{{display:block}}
td{{display:grid;grid-template-columns:minmax(90px,40%) 1fr;gap:10px}} td:before{{display:block;content:attr(data-label);font-weight:600}}
tbody tr + tr{{border-top:2px solid #e1dbde;margin-top:10px}}
tfoot td:first-child{{display:none}}}}
@media print{{body{{background:white;padding:0}} main{{padding:0;max-width:none}}}}
</style></head><body><main>
<header><p class="mark">Учебный документ</p><h1>{title}</h1><p class="meta">№ {number} от {created}</p>
<dl class="details"><div><dt>Получатель</dt><dd>{recipient}</dd></div><div><dt>Поставщик</dt><dd>{supplier}</dd></div></dl></header>
<table aria-label="Позиции документа">
<thead><tr><th scope="col">Артикул</th><th scope="col">Наименование</th><th scope="col">Кол-во</th>
<th scope="col">Цена за единицу</th><th scope="col">Скидка</th><th scope="col">Итог</th><th scope="col">В т. ч. НДС</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td colspan="5">Итого к согласованию</td>
<td data-label="Итого" colspan="2">{doc['total']:.2f} {currency}</td></tr></tfoot>
</table>
<h2>Условия</h2><dl class="details">{supplied}{bank}</dl>{note}
<footer><h2>Следующий шаг</h2><p>{instruction}</p>{card}<p class="template">{attachment}</p></footer>
</main></body></html>"""
