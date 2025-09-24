# app/telemetry/tracing.py
from __future__ import annotations
import os
import contextvars
from typing import Any, Optional

try:
    # v3 SDK
    from langfuse import Langfuse, get_client  # type: ignore
except Exception:
    Langfuse = None  # type: ignore
    def get_client():  # type: ignore
        return None

_current_client: contextvars.ContextVar[Any] = contextvars.ContextVar("lf_client", default=None)
_initialized = False
_last_error: Optional[str] = None
_debug = os.getenv("LANGFUSE_DEBUG") in {"1", "true", "True", "YES", "yes"}

def _init_client() -> Any:
    """Init once; return client or None. Safe if pkg/envs missing."""
    global _initialized, _last_error
    if _initialized:
        return _current_client.get()
    _initialized = True

    if Langfuse is None:
        _last_error = "langfuse package not installed"
        if _debug: print("[langfuse] package missing; no-op mode")
        return None

    # Prefer env-based init (recommended by v3 docs)
    try:
        client = get_client()
        # Optionally verify (sync; avoid in prod hot path)
        try:
            if client and hasattr(client, "auth_check") and not client.auth_check():
                _last_error = "auth_check failed"
                if _debug: print("[langfuse] auth_check failed")
        except Exception as e:
            # If host is wrong etc., keep the client but record last_error
            _last_error = f"auth_check error: {type(e).__name__}: {e}"
            if _debug: print(f"[langfuse] {_last_error}")

        _current_client.set(client)
        if _debug and client: print("[langfuse] initialized")
        return client
    except Exception as e:
        _last_error = f"init failed: {type(e).__name__}: {e}"
        if _debug: print(f"[langfuse] {_last_error}")
        _current_client.set(None)
        return None

def get_current_trace() -> Any:
    """For backward-compat with the rest of the code: returns the LF client or None."""
    if _current_client.get() is None:
        _init_client()
    return _current_client.get()

def set_current_trace(client: Any):
    """Store the client in a contextvar so nested code can start spans."""
    return _current_client.set(client)

def reset_current_trace(token) -> None:
    try:
        _current_client.reset(token)
    except Exception:
        pass

# ---------- Diagnostics ----------

def is_client_ready() -> bool:
    _init_client()
    return get_current_trace() is not None and _last_error is None

def diagnostics() -> dict:
    _init_client()
    client = get_current_trace()
    return {
        "installed": Langfuse is not None,
        "client_ready": client is not None,
        "last_error": _last_error,
        "has_current_trace": False,   # we don’t expose the active OTEL span here
        "current_trace_id": None,
        "env": {
            "LANGFUSE_PUBLIC_KEY": bool(os.getenv("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_PK")),
            "LANGFUSE_SECRET_KEY": bool(os.getenv("LANGFUSE_SECRET_KEY") or os.getenv("LANGFUSE_SK") or os.getenv("LANGFUSE_PRIVATE_KEY")),
            "LANGFUSE_HOST": os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_URL") or os.getenv("LANGFUSE_BASE_URL"),
        },
    }

def test_trace(name: str = "langfuse_v3_sanity", **kwargs) -> dict:
    """Write a root span + child span using v3 APIs."""
    global _last_error
    client = _init_client()
    if client is None:
        return {"ok": False, "error": _last_error or "client not initialized"}

    try:
        # Root span sets the active context
        with client.start_as_current_span(name=name, input={"ping": "pong"}, **kwargs) as root:
            # Child span (manual, still a child of root due to active context)
            child = client.start_span(name="sanity_child", input={"hello": "world"})
            child.end(output={"done": True})
            # Update root span output (trace attrs can be set via root.update_trace)
            root.update(output={"ok": True})
            # Best effort: expose IDs if present
            trace_id = getattr(root, "trace_id", None)
            obs_id = getattr(root, "id", None)
            return {"ok": True, "trace_id": trace_id, "root_observation_id": obs_id}
    except Exception as e:
        _last_error = f"test_trace failed: {type(e).__name__}: {e}"
        if _debug: print(f"[langfuse] {_last_error}")
        return {"ok": False, "error": _last_error}
