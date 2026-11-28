"""Identify independent buyer requests without splitting product attributes or decimal numbers."""
import re

from .selection import catalogue_text, facets_for


REQUEST = r"(?:как\w*|где|когда|сколько|зачем|почему|что|чем|есть|можно|дадите|нуж(?:ен|на|но)|подскажите|покажите|сравните|проверьте|подберите|запросите|подготовьте|выставьте|оформите|пришлите|расскажите|дайте|позовите|цена|скидк[ау]|срок\s+(?:поставки|обновлений|лицензии)|наличие)"
BOUNDARY = re.compile(r"(?<=[?!;])\s+|\n+|(?<=\.)\s+(?=(?:а\s+)?"+REQUEST+r"\b)|"
                      r"(?:,\s*(?:а\s+)?|\s+(?:и|а также|а)\s+)(?="+REQUEST+r"\b)", re.I)
CONDITION = re.compile(r"\b(?:если|при условии|только при|после подтверждения|после согласования)\b", re.I)
ISSUE = re.compile(r"\b(?:подготов|оформ|выстав|сдела|сформир|пришл|нужен|нужно)\w*\b[^?.!]{0,55}\b(?:сч[её]т|кп|коммерческ\w* предложени\w*)\b", re.I)
PERSON = re.compile(r"(?:позов|позва|позви|подключ|соедин|переключ|переда|хочу|нужен|можно|дайте|зовите)[^?.!]{0,40}"
                    r"(?:человек|оператор|менеджер|специалист)|^\s*(?:оператор[ау]?|менеджер[ау]?|человек[ау]?)[.!?\s]*$", re.I)


def parts(text):
    fragments = []
    for clause in BOUNDARY.split(catalogue_text(text).strip()):
        if not clause.strip():
            continue
        # An introductory statement names the subject of the next question.
        if fragments and CONDITION.match(fragments[-1]) and ISSUE.search(clause):
            fragments[-1] += ", " + clause.strip()
        elif fragments and fragments[-1].endswith(".") and (re.match(r"нуж(?:ен|на|но)\b", fragments[-1], re.I) or not re.search(r"\b"+REQUEST+r"\b", fragments[-1], re.I)):
            fragments[-1] += " " + clause.strip()
        elif (fragments and not re.search(r"[?!]|\b"+REQUEST+r"\b", fragments[-1], re.I)
              and (facets_for(fragments[-1]).get("product") or facets_for(fragments[-1]).get("article"))):
            fragments[-1] += ", " + clause.strip()
        else:
            fragments.append(clause.strip())
    # A shared object at the end belongs to both predicates.
    merged = []
    for fragment in fragments:
        if merged and re.fullmatch(r"сколько сто(?:ит|ят)|какая цена", merged[-1], re.I) and re.search(r"наличи|доступ", fragment, re.I):
            merged[-1] += " и " + fragment
        else:
            merged.append(fragment)
    return merged

# Each predicate is bound to its own published catalog field.
PROPERTIES = (
    (re.compile(r"срок.{0,18}обновлен|как долго.{0,20}обновлен", re.I), "Срок обновлений"),
    (re.compile(r"срок.{0,18}(?:действия|лицензии)|на какой срок", re.I), "Срок действия"),
    (re.compile(r"срок.{0,12}поставки|когда.{0,15}постав", re.I), "Срок поставки"),
    (re.compile(r"способ.{0,12}поставки|как поставляется", re.I), "Тип поставки"),
    (re.compile(r"кто (?:вендор|производитель)|какой производитель", re.I), "Производитель"),
)
