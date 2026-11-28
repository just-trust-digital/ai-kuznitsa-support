"""Measurements and staff context at the end of automated support."""


def handoff_summary(dialog):
    messages = dialog["messages"]
    questions = [m["text"].strip() for m in messages if m["role"] == "buyer" and m["text"].strip()]
    lines = []
    if questions:
        lines.append("Запрос: " + questions[0][:280])
    if questions and questions[-1] != questions[0]:
        lines.append("Последнее сообщение: " + questions[-1][:240])
    items = dict.fromkeys(m["reference"]["label"] for m in messages if m.get("reference", {}).get("label"))
    if items:
        lines.append("По обращению: " + ", ".join(items)[:240])
    facets = dialog.get("facets", {})
    details = [f"{title}: {facets[key]}" for key, title in
               (("product", "Продукт"), ("article", "Артикул"), ("seats", "Количество"),
                ("platform", "Платформа"), ("edition", "Редакция")) if facets.get(key)]
    if details:
        lines.append(". ".join(details)[:280])
    previous = next((m for m in reversed(messages) if m["role"] == "assistant"
                     and m.get("decision", {}).get("next_action") in {"inform", "lookup", "shortlist"}), None)
    if previous:
        lines.append("Ответ помощника: " + previous["text"][:320])
    pending = [part["fragment"] for part in dialog.get("decision", {}).get("parts", [])
               if part.get("action") in {"handoff", "clarify"}]
    if pending:
        lines.append("Осталось уточнить: " + "; ".join(pending)[:360])
    lines.append("Причина передачи: " + dialog["route"]["reason"])
    return "\n".join(lines)


def record(dialog, action, elapsed_ms, model, *, confirmed=False):
    if dialog.get("outcome"):
        return
    if action in {"message", "handoff", "price_request", "proposal", "invoice"}:
        metrics = dialog.setdefault("response_metrics", {"turns": 0, "ms": 0, "usage": {}})
        metrics["turns"] += 1
        metrics["ms"] += elapsed_ms
        readings = (model or {}).get("parts", {}).values() if (model or {}).get("parts") else [model or {}]
        for response in readings:
            for provider, reading in response.get("usage", {}).items():
                totals = metrics["usage"].setdefault(provider, {})
                for field in ("calls", "metered_calls", "input_tokens", "output_tokens"):
                    totals[field] = totals.get(field, 0) + (reading.get(field) or 0)
    if dialog.get("route") and dialog["status"] in {"assigned", "queued"}:
        dialog["outcome"] = "handoff"
        dialog["handoff_summary"] = handoff_summary(dialog)
    elif action == "end_chat" and confirmed and not dialog.get("route"):
        dialog["outcome"] = "autonomous"
