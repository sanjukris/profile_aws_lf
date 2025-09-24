from __future__ import annotations
import os
import asyncio
from typing import Any, Dict
# import httpx # commented out because we are mocking all API calls
from strands import tool
import time
from app.telemetry.tracing import get_current_trace

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
API_BASE = os.getenv("PROFILE_API_BASE", "https://uat.api.securecloud.tbd.com").rstrip("/")
API_KEY = os.getenv("PROFILE_API_KEY", "tbd")
BASIC_AUTH = os.getenv("PROFILE_BASIC_AUTH", "tbd")
SCOPE = os.getenv("PROFILE_SCOPE", "public")
PREF_USERNM = os.getenv("PROFILE_USERNM", "test")


# ------------------------------------------------------------------
# Mock payloads (from your prompt file samples)
# ------------------------------------------------------------------
ACCESS = {"access_token": "tbd"}


EMAIL = {
    "email": [
        {
            "emailTypeCd": {"code": "EMAIL1", "name": "EMAIL 1", "desc": "EMAIL 1"},
            "emailUid": "1750954079330009717442120",
            "emailStatusCd": {"code": "BLANK", "name": "Blank", "desc": "..."},
            "emailAddress": "SAMPLEEMAILID_1@SAMPLEDOMAIN.COM",
        }
    ]
}


ADDR = {
    "address": [
        {
            "addressTypeCd": {"code": "HOME", "name": "Home"},
            "addressLineOne": "1928288 DO NOT MAIL",
            "city": "AVON LAKE",
            "stateCd": {"code": "OH"},
            "countryCd": {"code": "US"},
            "countyCd": {"code": "093"},
            "zipCd": "44012",
            "addressUid": "1733664015649003100039610",
        }
    ]
}


PREFS = {
    "memberPreference": [
        {
            "preferenceUid": "HRA",
            "preferenceTypeCd": {"code": "HRA", "name": "HRA Indicator"},
            "defaulted": "true",
            "clearSelection": "false",
            "allowClearSelection": "false",
            "terminationDt": "9999-12-31 00:00:00.000",
        }
    ]
}

# ------------------------------------------------------------------
# Mocked HTTP helpers (commented out httpx calls)
# ------------------------------------------------------------------
async def _get_access_token_async() -> str:
    client = get_current_trace()
    span = client.start_span(name="access_token") if client else None
    t0 = time.perf_counter()
# url = f"{API_BASE}/v1/oauth/accesstoken"
# headers = {
# "apikey": API_KEY,
# "Authorization": f"Basic {BASIC_AUTH}",
# "Content-Type": "application/x-www-form-urlencoded",
# }
# data = {"grant_type": "client_credentials", "scope": SCOPE}
# async with httpx.AsyncClient() as client:
# r = await client.post(url, headers=headers, data=data, timeout=20)
# r.raise_for_status()
# token = r.json().get("access_token")
# return token or ""
    dt = (time.perf_counter() - t0) * 1000
    print(f"[timing] access_token: {dt:.1f} ms")
    if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
    return ACCESS["access_token"]


async def _get_email_async(member_id: str, bearer: str) -> Dict[str, Any]:
    client = get_current_trace()
    span = client.start_span(name="get_email", input={"member_id": member_id}) if client else None

    t0 = time.perf_counter()
# url = f"{API_BASE}/genai/v1/{member_id}/email"
# headers = {"apikey": API_KEY, "Authorization": f"Bearer {bearer}"}
# async with httpx.AsyncClient() as client:
# r = await client.get(url, headers=headers, timeout=20)
# r.raise_for_status()
# return r.json()
    dt = (time.perf_counter() - t0) * 1000
    print(f"[timing] email: {dt:.1f} ms")
    if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
    return EMAIL


async def _get_address_async(member_id: str, bearer: str) -> Dict[str, Any]:
    trace = get_current_trace()
    client = get_current_trace()
    span = client.start_span(name="get_address", input={"member_id": member_id}) if client else None
    t0 = time.perf_counter()
# url = f"{API_BASE}/genai/v1/{member_id}/address"
# headers = {"apikey": API_KEY, "Authorization": f"Bearer {bearer}"}
# async with httpx.AsyncClient() as client:
# r = await client.get(url, headers=headers, timeout=20)
# r.raise_for_status()
# return r.json()
    dt = (time.perf_counter() - t0) * 1000
    print(f"[timing] address: {dt:.1f} ms")
    if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
    return ADDR


async def _get_preferences_async(member_id: str, bearer: str) -> Dict[str, Any]:
    trace = get_current_trace()
    client = get_current_trace()
    span = client.start_span(name="get_preferences", input={"member_id": member_id}) if client else None
    t0 = time.perf_counter()
# url = f"{API_BASE}/genai/v1/{member_id}/preferences"
# headers = {"apikey": API_KEY, "Authorization": f"Bearer {bearer}", "usernm": PREF_USERNM}
# async with httpx.AsyncClient() as client:
# r = await client.get(url, headers=headers, timeout=20)
# r.raise_for_status()
# return r.json()
    dt = (time.perf_counter() - t0) * 1000
    print(f"[timing] preferences: {dt:.1f} ms")
    if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
    return PREFS

# ------------------------------------------------------------------
# Tools (direct calls by the agent)
# ------------------------------------------------------------------
@tool
def fetch_email_and_address(*, member_id: str) -> Dict[str, Any]:
    async def run() -> Dict[str, Any]:
        client = get_current_trace()
        span = client.start_span(name="tool.fetch_email_and_address", input={"member_id": member_id}) if client else None
        t0 = time.perf_counter()
        token = await _get_access_token_async()
        email_json = await _get_email_async(member_id, token)     # returns EMAIL mock
        address_json = await _get_address_async(member_id, token) # returns ADDR mock
        dt = (time.perf_counter() - t0) * 1000
        print(f"[timing] fetch_email_and_address: {dt:.1f} ms")
        if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
        # IMPORTANT: return the keys the agent expects
        return {"email_json": email_json, "address_json": address_json}
    return asyncio.run(run())


@tool
def fetch_contact_preference(*, member_id: str) -> Dict[str, Any]:
    async def run() -> Dict[str, Any]:
        client = get_current_trace()
        span = client.start_span(name="tool.fetch_contact_preference", input={"member_id": member_id}) if client else None
        t0 = time.perf_counter()
        token = await _get_access_token_async()
        prefs = await _get_preferences_async(member_id, token)    # returns PREFS mock
        dt = (time.perf_counter() - t0) * 1000
        print(f"[timing] fetch_contact_preference: {dt:.1f} ms")
        if span: span.end(output={"status": "ok", "ms": round(dt, 1)})
        # IMPORTANT: return the key the agent expects
        return {"preferences_json": prefs}
    return asyncio.run(run())
