from __future__ import annotations
from typing import Any, Dict, List, Tuple
from pydantic import ValidationError
from strands import Agent

# NEW: robust parsing & extraction utilities
import json, ast, time
from collections.abc import Mapping, Sequence

from app.schemas.profile_schemas import (
    NameValue,
    Journey,
    Header,
    EntitiesEmailAddr,
    EmailAddressBlock,
    ProfileOverviewResponse,
    PreferencesOverviewResponse,
    PreferencesData,
    PreferenceItem,
)

from app.tools.profile_tools import (
    fetch_email_and_address,
    fetch_contact_preference,
)
from app.telemetry.tracing import get_current_trace

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# ------------------------------------------------------------------
# Agent construction
# ------------------------------------------------------------------

def create_profile_agent() -> Agent:
    return Agent(
        model=MODEL_ID,
        tools=[fetch_email_and_address, fetch_contact_preference],
        callback_handler=None,
        record_direct_tool_call=False,
    )

# ------------------------------------------------------------------
# Intent routing (fast, deterministic)
# ------------------------------------------------------------------

def _classify(query: str) -> str:
    q = (query or "").lower()
    email_addr_hits = any(
        w in q
        for w in [
            "email",
            "e-mail",
            "mail id",
            "postal address",
            "mailing address",
            "address",
            "zip",
            "city",
            "state",
        ]
    )
    pref_hits = any(
        w in q
        for w in [
            "preference",
            "preferences",
            "contact method",
            "notifications",
            "sms",
            "text",
            "eob",
            "language",
            "digital wallet",
        ]
    )
    if email_addr_hits and not pref_hits:
        return "fetch_email_and_address"
    if pref_hits and not email_addr_hits:
        return "fetch_contact_preference"
    return "fetch_email_and_address" if email_addr_hits else "fetch_contact_preference"

# ------------------------------------------------------------------
# Tool result unwrapping & robust extractors
# ------------------------------------------------------------------

def _unwrap_tool_result(raw: Any) -> Any:
    """Normalize Strands tool results into a plain dict.
    Supports shapes like {content:[{json: {...}}]} or {content:[{text: "{...}"}]}.
    """
    if isinstance(raw, dict) and isinstance(raw.get("content"), list):
        for part in raw["content"]:
            if isinstance(part, dict):
                if "json" in part and isinstance(part["json"], dict):
                    return part["json"]
                if "text" in part and isinstance(part["text"], str):
                    s = part["text"].strip()
                    # Try JSON first, then Python-literal fallback
                    try:
                        return json.loads(s)
                    except json.JSONDecodeError:
                        try:
                            return ast.literal_eval(s)
                        except Exception:
                            return {}
    return raw


def _walk_dicts(obj):
    """Yield every dict inside obj recursively."""
    if isinstance(obj, Mapping):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        for v in obj:
            yield from _walk_dicts(v)


def _first_dict_with_keys(obj, required_any=None, required_all=None):
    required_any = set(required_any or [])
    required_all = set(required_all or [])
    for d in _walk_dicts(obj):
        keys = set(d.keys())
        if required_all and not required_all.issubset(keys):
            continue
        if required_any and not (required_any & keys):
            continue
        return d
    return {}


def _extract_first_email(email_json):
    if isinstance(email_json, Mapping):
        arr = email_json.get("email") or email_json.get("emails")
        if isinstance(arr, list) and arr:
            return arr[0]
    return _first_dict_with_keys(email_json, required_any={"emailAddress", "emailUid"})


def _extract_first_address(address_json):
    if isinstance(address_json, Mapping):
        arr = address_json.get("address") or address_json.get("addresses")
        if isinstance(arr, list) and arr:
            return arr[0]
    return _first_dict_with_keys(
        address_json,
        required_any={"addressLineOne", "city", "stateCd", "zipCd", "addressUid"},
    )


def _extract_preferences_list(preferences_json):
    if not isinstance(preferences_json, Mapping):
        return []
    if isinstance(preferences_json.get("memberPreference"), list):
        return preferences_json["memberPreference"]
    prefs = preferences_json.get("preferences")
    if isinstance(prefs, Mapping) and isinstance(prefs.get("memberPreference"), list):
        return prefs["memberPreference"]
    items = []
    for d in _walk_dicts(preferences_json):
        if isinstance(d, Mapping) and ("preferenceUid" in d or "preferenceTypeCd" in d):
            items.append(d)
    return items

# ------------------------------------------------------------------
# Output assembly helpers
# ------------------------------------------------------------------

def _build_email_address_output(
    member_id: str, email_json: Dict[str, Any], address_json: Dict[str, Any]
) -> ProfileOverviewResponse:
    email_first = _extract_first_email(email_json) or {}
    addr_first = _extract_first_address(address_json) or {}

    header = Header(
        title=f"Your profile for {member_id}",
        description="Profile overview with primary email and address",
    )
    journey = Journey(
        journey="MANAGE_PROFILE",
        subjourney="ENSURE_VALID_PROFILE",
        task="CHECK_PROFILE",
        subtask="PROFILE_OVERVIEW",
    )
    entities: List[EntitiesEmailAddr] = [
        EntitiesEmailAddr(name="emailUid", value=email_first.get("emailUid")),
        EntitiesEmailAddr(name="addressUid", value=addr_first.get("addressUid")),
    ]

    def _code(x):
        return (x or {}).get("code") if isinstance(x, Mapping) else None

    data = EmailAddressBlock(
        email=[NameValue(name="Email Address: ", value=email_first.get("emailAddress"))],
        address=[
            NameValue(name="Address Type Cd", value=_code(addr_first.get("addressTypeCd"))),
            NameValue(name="Address Line One: ", value=addr_first.get("addressLineOne")),
            NameValue(name="Care Of: ", value=addr_first.get("careOf")),
            NameValue(name="City: ", value=addr_first.get("city")),
            NameValue(name="StateCd: ", value=_code(addr_first.get("stateCd"))),
            NameValue(name="CountryCd: ", value=_code(addr_first.get("countryCd"))),
            NameValue(name="CountyCd: ", value=_code(addr_first.get("countyCd"))),
            NameValue(name="ZipCd: ", value=addr_first.get("zipCd")),
            NameValue(name="ZipCdExt: ", value=addr_first.get("zipCdExt")),
        ],
    )
    return ProfileOverviewResponse(
        user_journey=journey, header=header, entities=entities, data=data
    )


def _build_preferences_output(
    member_id: str, preferences_json: Dict[str, Any]
) -> PreferencesOverviewResponse:
    items = _extract_preferences_list(preferences_json) or []

    header = Header(
        title=f"Contact preferences for {member_id}",
        description="Member communication and channel preferences",
    )
    journey = Journey(
        journey="MANAGE_PROFILE",
        subjourney="CONTACT_PREFERENCES",
        task="CHECK_PREFERENCES",
        subtask="PREFERENCES_OVERVIEW",
    )
    entities: List[EntitiesEmailAddr] = [
        EntitiesEmailAddr(name="preferenceCount", value=str(len(items)))
    ]

    data = PreferencesData(preferences=[PreferenceItem(**it) for it in items])
    return PreferencesOverviewResponse(
        user_journey=journey, header=header, entities=entities, data=data
    )

# ------------------------------------------------------------------
# Public entry
# ------------------------------------------------------------------

def handle_request(*, query: str, member_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Routes the query, calls the right tool, builds a validated response,
    emits Langfuse v3 spans when a client is available, and prints timing.
    Requires:
      - create_profile_agent()
      - _classify(), _unwrap_tool_result()
      - _build_email_address_output(), _build_preferences_output()
      - get_current_trace() from app.telemetry.tracing
      - time imported
    """
    client = get_current_trace()  # Langfuse v3 client or None
    span_outer = client.start_span(
        name="handle_request",
        input={"query": query, "member_id": member_id},
    ) if client else None

    t0 = time.perf_counter()
    agent = create_profile_agent()
    t_agent = time.perf_counter()

    intent = _classify(query)

    if intent == "fetch_email_and_address":
        # tool span
        span_tool = client.start_span(
            name="tool_call",
            input={"tool": "fetch_email_and_address", "member_id": member_id},
        ) if client else None

        t_tool0 = time.perf_counter()
        raw = agent.tool.fetch_email_and_address(
            member_id=member_id,
            record_direct_tool_call=False,
        )
        t_tool1 = time.perf_counter()
        if span_tool:
            try:
                span_tool.end(output={"status": "ok", "ms": round((t_tool1 - t_tool0) * 1000, 1)})
            except Exception:
                pass

        raw = _unwrap_tool_result(raw)

        # build span
        span_build = client.start_span(
            name="build_output",
            input={"schema": "ProfileOverviewResponse"},
        ) if client else None

        t_build0 = time.perf_counter()
        try:
            out = _build_email_address_output(
                member_id,
                raw.get("email_json"),
                raw.get("address_json"),
            )
        except ValidationError as ve:
            if span_build:
                try:
                    span_build.end(output={"status": "validation_error", "message": str(ve)[:500]})
                except Exception:
                    pass
            raise RuntimeError(f"Validation failed for ProfileOverviewResponse: {ve}") from ve
        t_end = time.perf_counter()

        if span_build:
            try:
                span_build.end(output={"status": "ok", "ms": round((t_end - t_build0) * 1000, 1)})
            except Exception:
                pass

        print(
            f"[timing] handle_request[{intent}]: "
            f"agent_init={(t_agent - t0)*1000:.1f} ms, "
            f"tool={(t_tool1 - t_tool0)*1000:.1f} ms, "
            f"build={(t_end - t_build0)*1000:.1f} ms, "
            f"total={(t_end - t0)*1000:.1f} ms"
        )
        if span_outer:
            try:
                span_outer.end(output={"intent": intent, "total_ms": round((t_end - t0) * 1000, 1)})
            except Exception:
                pass
        return intent, out.model_dump()

    # preferences branch
    span_tool = client.start_span(
        name="tool_call",
        input={"tool": "fetch_contact_preference", "member_id": member_id},
    ) if client else None

    t_tool0 = time.perf_counter()
    raw = agent.tool.fetch_contact_preference(
        member_id=member_id,
        record_direct_tool_call=False,
    )
    t_tool1 = time.perf_counter()
    if span_tool:
        try:
            span_tool.end(output={"status": "ok", "ms": round((t_tool1 - t_tool0) * 1000, 1)})
        except Exception:
            pass

    raw = _unwrap_tool_result(raw)

    span_build = client.start_span(
        name="build_output",
        input={"schema": "PreferencesOverviewResponse"},
    ) if client else None

    t_build0 = time.perf_counter()
    try:
        out = _build_preferences_output(member_id, raw.get("preferences_json"))
    except ValidationError as ve:
        if span_build:
            try:
                span_build.end(output={"status": "validation_error", "message": str(ve)[:500]})
            except Exception:
                pass
        raise RuntimeError(f"Validation failed for PreferencesOverviewResponse: {ve}") from ve
    t_end = time.perf_counter()

    if span_build:
        try:
            span_build.end(output={"status": "ok", "ms": round((t_end - t_build0) * 1000, 1)})
        except Exception:
            pass

    print(
        f"[timing] handle_request[{intent}]: "
        f"agent_init={(t_agent - t0)*1000:.1f} ms, "
        f"tool={(t_tool1 - t_tool0)*1000:.1f} ms, "
        f"build={(t_end - t_build0)*1000:.1f} ms, "
        f"total={(t_end - t0)*1000:.1f} ms"
    )
    if span_outer:
        try:
            span_outer.end(output={"intent": intent, "total_ms": round((t_end - t0) * 1000, 1)})
        except Exception:
            pass
    return intent, out.model_dump()


if __name__ == "__main__":
    tool, result = handle_request(
        query="show my email and mailing address", member_id="378477398"
    )
    print(tool)
    print(result)
