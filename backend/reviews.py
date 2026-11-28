"""Staff review queue and side-effect-free replay inputs."""

from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit


PRIVATE_TEXT = (
    r"\b[\w.+-]+@[\w.-]+\.[a-zа-я]{2,}\b",
    r"(?<!\w)(?:\+?\d[\s()\-]*){10,}(?!\w)",
    r"\b(?:sl|inv|kp)-[\w-]+\b",
    r"(?<=\bключ\s)[A-Z0-9-]{10,}\b",
    r"\b(?:DEMO-LIC|DEMO-KEY)-[A-Z0-9-]+\b",
    r"\b(?:Bearer\s+|(?:пароль|токен|api[_ -]?key|ключ активации)\s*[:=]\s*)[^\s,;]+",
    r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b",
)
ELIGIBLE_ROLES = {"assistant", "manager1", "manager2"}


def redact(text):
    for pattern in PRIVATE_TEXT:
        text = re.sub(pattern, "[скрыто]", text, flags=re.IGNORECASE)
    return text


def source_reference(source):
    if not isinstance(source, str):
        return None
    try:
        parts = urlsplit(source)
    except ValueError:
        return None
    if parts.scheme in {"http", "https"} and parts.hostname and not parts.username:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return source if source in {"xml", "synthetic", "rules"} else None


def can_review(role, dialog):
    if dialog.get("demo_archived"):
        return False
    if role == "developer":
        return True
    if role not in {"manager1", "manager2"}:
        return False
    if dialog.get("assignment"):
        return dialog["assignment"] == role
    return dialog.get("status") == "open" and not dialog.get("route")


def candidates(dialog):
    question = None
    context = {}
    for message in dialog["messages"]:
        if message.get("role") == "buyer" and message.get("text", "").strip():
            question = message
        elif question and message.get("role") in ELIGIBLE_ROLES and message.get("text", "").strip():
            decision = message.get("decision", {})
            if decision.get("next_action") != "handoff" and not message.get("document_id"):
                yield question, message, context
            if decision:
                context = decision


def record(dialog_id, question, message, previous=None):
    keys = ("id", "text", "source", "template_id", "mode")
    material = json.dumps({key: message.get(key) for key in keys}, sort_keys=True, ensure_ascii=False)
    return dict(dialog_id=dialog_id, message_id=message["id"], question=redact(question["text"]),
                answer=redact(message["text"]), source=source_reference(message.get("source")),
                version=hashlib.sha256(material.encode("utf-8")).hexdigest()[:16],
                previous_rating=previous)


def queue(state, role, limit=50):
    if role == "buyer":
        return []
    previous = {row["message_id"]: row["rating"] for row in state["reviews"] if row.get("kind") == "rating"}
    ranked = []
    for dialog in state["dialogs"].values():
        if can_review(role, dialog):
            for question, answer, _ in candidates(dialog):
                ranked.append((answer.get("at", ""), dialog["id"], question, answer))
    ranked.reverse()
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [record(dialog_id, question, answer, previous.get(answer["id"]))
            for _, dialog_id, question, answer in ranked[:limit]]


def lookup(state, role, dialog_id, message_id, requested_version=""):
    dialog = state["dialogs"][dialog_id]
    if not can_review(role, dialog):
        raise PermissionError("Диалог недоступен для ревью")
    for question, message, context in candidates(dialog):
        if message["id"] == message_id:
            item = record(dialog_id, question, message)
            if requested_version and requested_version != item["version"]:
                raise ValueError("Версия ответа изменилась. Откройте очередь заново.")
            return item, question, context
    raise KeyError(message_id)


def log_for(state, role):
    if role == "buyer":
        return []
    if role == "developer":
        return state["reviews"]
    return [row for row in state["reviews"] if row["role"] == role and (
        not row.get("dialog_id") or can_review(role, state["dialogs"].get(row["dialog_id"], {}))
    )]


def capture_context(dialog):
    keys = ("facets", "viewed_offer", "viewed_group", "group_request", "decision", "discount_counteroffer", "purchase_condition",
            "optional_answers", "guided_selection", "search_by_name", "asked_seats", "asked_platform", "clarification_stalled")
    return {key: deepcopy(dialog[key]) for key in keys if key in dialog}


def trace_steps(messages, fragment):
    steps = []
    for message in messages:
        decision = message.get("decision", {})
        steps.append(dict(text=redact(message["text"]), fragment=redact(message.get("review_fragment", fragment)),
                          evidence=[redact(str(e)) for e in decision.get("evidence", [])],
                          intent=decision.get("intent"),
                          action=decision.get("next_action"), missing=decision.get("missing", []),
                          source=source_reference(message.get("source")), template=message.get("template_id")))
    return steps


def replay(state, role, dialog_id, message_id, version, answer):
    item, question, context = lookup(state, role, dialog_id, message_id, version)
    original = state["dialogs"][dialog_id]
    scenario = deepcopy(state)
    scenario["buyer_id"] = original.get("buyer_id", "buyer1")
    original_answer = next(m for m in original["messages"] if m["id"] == message_id)
    staged = dict(id="review-replay", assignment=None, status="open", route=None,
                  messages=[dict(question)], facets=dict(context.get("facets", {})), decision=context)
    history = original["messages"][:original["messages"].index(question)+1]
    staged["messages"] = deepcopy(history)
    if "discount_acceptance" in context.get("missing", []):
        staged["discount_counteroffer"] = True
    staged.update(deepcopy(question.get("review_context", {})))
    answer(scenario, staged, question)
    steps = trace_steps(staged["messages"][len(history):], question["text"])
    variants = original_answer.get("review_variants", {})
    model = original_answer.get("model") or {}
    response = staged["messages"][-1]
    next_action = staged.get("decision", {}).get("next_action")
    result = {key: item[key] for key in ("dialog_id", "message_id", "version", "question")}
    result.update(steps=steps, variants=variants, original_template=original_answer.get("template_id"),
                  model_published=bool(model.get("published")), model_failed=bool(model.get("error")))
    result.update(answer="В этом сценарии запрос требует менеджера." if next_action == "handoff"
                  else "\n".join(dict.fromkeys(step["text"] for step in steps)),
                  source=source_reference(response.get("source")), next_action=next_action, mode="rules")
    return result
