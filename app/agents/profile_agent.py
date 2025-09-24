from __future__ import annotations
from typing import Any, Dict, Tuple
from pydantic import ValidationError
from strands import Agent

import time

from app.tools.profile_tools import (
    fetch_email_and_address,
    fetch_contact_preference,
)
from app.telemetry.tracing import get_current_trace

# utils
from app.utils.intent import classify_intent
from app.utils.json_utils import unwrap_tool_result
from app.utils.builders import (
    build_email_address_output,
    build_preferences_output,
)

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

def create_profile_agent() -> Agent:
    return Agent(
        model=MODEL_ID,
        tools=[fetch_email_and_address, fetch_contact_preference],
        callback_handler=None,
        record_direct_tool_call=False,
    )

def handle_request(*, query: str, member_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Routes the query, calls the right tool, builds a validated response,
    emits Langfuse v3 spans when a client is available, and prints timing.
    """
    client = get_current_trace()  # Langfuse v3 client or None
    span_outer = client.start_span(
        name="handle_request",
        input={"query": query, "member_id": member_id},
    ) if client else None

    t0 = time.perf_counter()
    agent = create_profile_agent()
    t_agent = time.perf_counter()

    intent = classify_intent(query)

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

        raw = unwrap_tool_result(raw)

        # build span
        span_build = client.start_span(
            name="build_output",
            input={"schema": "ProfileOverviewResponse"},
        ) if client else None

        t_build0 = time.perf_counter()
        try:
            out = build_email_address_output(
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

    raw = unwrap_tool_result(raw)

    span_build = client.start_span(
        name="build_output",
        input={"schema": "PreferencesOverviewResponse"},
    ) if client else None

    t_build0 = time.perf_counter()
    try:
        out = build_preferences_output(member_id, raw.get("preferences_json"))
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
