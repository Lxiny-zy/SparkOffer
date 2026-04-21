"""Incremental JSON array parser for streaming LLM output."""
import json
import re


def extract_complete_objects(partial_json: str) -> tuple[list[dict], str]:
    """Extract complete JSON objects from partial streaming output.

    Returns (list of complete objects, remaining unparsed text).
    """
    cleaned = partial_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    if not cleaned.startswith("["):
        idx = cleaned.find("[")
        if idx == -1:
            return [], partial_json
        cleaned = cleaned[idx:]

    objects = []
    depth = 0
    in_string = False
    escape = False
    obj_start = -1

    for i, ch in enumerate(cleaned):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                obj_str = cleaned[obj_start:i + 1]
                try:
                    obj = json.loads(obj_str)
                    objects.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = -1

    if obj_start >= 0:
        remaining = cleaned[obj_start:]
    elif objects:
        last_close = cleaned.rfind("}")
        remaining = cleaned[last_close + 1:] if last_close >= 0 else ""
    else:
        remaining = partial_json

    return objects, remaining
