"""Calculate support costs from verified rates and aggregate measurements."""

import json
import sys
from decimal import Decimal, InvalidOperation


def decimal(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Expected a non-negative number")
    if number.is_finite() and number >= 0:
        return number
    raise ValueError("Expected a non-negative number")


def estimate(profile, volume):
    missing = []
    measured_dialogues = decimal(profile.get("measured_dialogues"))
    if not measured_dialogues:
        missing.append("measured_dialogues")
    measured_requests = decimal(profile.get("measured_requests"))
    if not measured_requests:
        missing.append("measured_requests")
    terms = {}
    for key in ("fixed_month", "local_compute_per_dialogue", "operator_minutes_per_dialogue", "operator_hourly"):
        terms[key] = decimal(profile.get(key))
        if terms[key] is None:
            missing.append(key)
    cloud_cost = Decimal(0)
    usage = profile.get("usage")
    if usage is None:
        missing.append("usage")
    for provider, counters in (usage or {}).items():
        if provider not in {"jev", "cloud"}:
            continue
        calls = decimal(counters.get("calls"))
        metered = decimal(counters.get("metered_calls"))
        if calls is None or calls != metered:
            missing.append(f"{provider}.metering")
            continue
        rates = profile.get("rates", {}).get(provider, {})
        for direction in ("input", "output"):
            tokens = decimal(counters.get(f"{direction}_tokens"))
            rate = decimal(rates.get(f"{direction}_per_million"))
            if tokens is None or (tokens and rate is None):
                missing.append(f"{provider}.{direction}_tokens_or_rate")
            else:
                cloud_cost += (rate or Decimal(0)) * tokens / Decimal(1_000_000)
    if missing:
        return dict(missing=missing)
    fixed, compute = terms["fixed_month"], terms["local_compute_per_dialogue"]
    labour = terms["operator_minutes_per_dialogue"] * terms["operator_hourly"] / 60
    variable = cloud_cost / measured_dialogues + compute + labour
    per_request = variable * measured_dialogues / measured_requests
    return dict(fixed_month=fixed, variable_per_dialogue=variable, variable_per_request=per_request,
                per_dialogue_at_volume=fixed / volume + variable if volume else None,
                per_request_at_volume=per_request + fixed * measured_dialogues / (volume * measured_requests)
                if volume else None, monthly=fixed + variable * volume)


def compare(data):
    volume = decimal(data["dialogues_per_month"])
    if volume is None:
        raise ValueError("Provide dialogues_per_month")
    profiles = {name: estimate(data.get(name, {}), volume) for name in ("local", "hybrid")}
    local, hybrid = profiles["local"], profiles["hybrid"]
    result = dict(currency=data["currency"], dialogues_per_month=volume, **profiles,
                  breakeven_dialogues_per_month=None, breakeven_status="unknown")
    if "missing" in local or "missing" in hybrid:
        return result
    fixed_difference = hybrid["fixed_month"] - local["fixed_month"]
    variable_difference = local["variable_per_dialogue"] - hybrid["variable_per_dialogue"]
    if not variable_difference:
        result["breakeven_status"] = "equal_at_every_volume" if not fixed_difference else "none"
    else:
        point = fixed_difference / variable_difference
        result["breakeven_status"] = "finite" if point >= 0 else "none"
        result["breakeven_dialogues_per_month"] = point if point >= 0 else None
    return result


if __name__ == "__main__":
    print(json.dumps(compare(json.load(sys.stdin)), ensure_ascii=False, indent=2, default=str))
