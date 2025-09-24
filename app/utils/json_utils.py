from __future__ import annotations
import json, ast
from typing import Any, Mapping, Sequence, Dict

def unwrap_tool_result(raw: Any) -> Any:
    """
    Normalize Strands tool result into a plain dict.
    Handles shapes like:
      { "content": [ { "json": {...} } ] }
      { "content": [ { "text": "{...}" } ] }
      or already a dict.
    """
    if isinstance(raw, dict) and isinstance(raw.get("content"), list):
        for part in raw["content"]:
            if isinstance(part, dict):
                if "json" in part and isinstance(part["json"], dict):
                    return part["json"]
                if "text" in part and isinstance(part["text"], str):
                    s = part["text"].strip()
                    try:
                        return json.loads(s)
                    except json.JSONDecodeError:
                        try:
                            return ast.literal_eval(s)
                        except Exception:
                            return {}
    return raw

def _walk_dicts(obj: Any):
    """Yield every dict inside obj recursively."""
    if isinstance(obj, Mapping):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        for v in obj:
            yield from _walk_dicts(v)

def first_dict_with_keys(obj: Any, required_any=None, required_all=None) -> Dict[str, Any]:
    """Find the first dict having any/all of the required keys."""
    required_any = set(required_any or [])
    required_all = set(required_all or [])
    for d in _walk_dicts(obj):
        keys = set(d.keys())
        if required_all and not required_all.issubset(keys):
            continue
        if required_any and not (required_any & keys):
            continue
        return dict(d)
    return {}

def extract_first_email(email_json: Any) -> Dict[str, Any]:
    if isinstance(email_json, Mapping):
        arr = email_json.get("email") or email_json.get("emails")
        if isinstance(arr, list) and arr:
            return dict(arr[0])
    return first_dict_with_keys(email_json, required_any={"emailAddress", "emailUid"})

def extract_first_address(address_json: Any) -> Dict[str, Any]:
    if isinstance(address_json, Mapping):
        arr = address_json.get("address") or address_json.get("addresses")
        if isinstance(arr, list) and arr:
            return dict(arr[0])
    return first_dict_with_keys(
        address_json,
        required_any={"addressLineOne", "city", "stateCd", "zipCd", "addressUid"},
    )

def extract_preferences_list(preferences_json: Any) -> list:
    """Return a list of preference items from many possible shapes."""
    if not isinstance(preferences_json, Mapping):
        return []
    if isinstance(preferences_json.get("memberPreference"), list):
        return list(preferences_json["memberPreference"])
    prefs = preferences_json.get("preferences")
    if isinstance(prefs, Mapping) and isinstance(prefs.get("memberPreference"), list):
        return list(prefs["memberPreference"])
    items = []
    for d in _walk_dicts(preferences_json):
        if isinstance(d, Mapping) and ("preferenceUid" in d or "preferenceTypeCd" in d):
            items.append(dict(d))
    return items
