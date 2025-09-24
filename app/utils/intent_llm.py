from __future__ import annotations
import os, re, json
from typing import Optional
from strands import Agent
from app.utils.intent_keywords import classify_intent_keywords

_ALLOWED = {"fetch_email_and_address", "fetch_contact_preference"}

_SYSTEM_PROMPT = """\
You are a router. Classify the user's request into exactly one of:
- fetch_email_and_address
- fetch_contact_preference

Rules:
- Return ONLY the matching identifier above.
- Do not include extra words or explanation.
"""

def _parse_intent(text: str) -> Optional[str]:
    if not text:
        return None
    # try strict JSON first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            v = str(obj.get("intent", "")).strip()
            if v in _ALLOWED:
                return v
    except Exception:
        pass
    # then regex for the allowed tokens
    m = re.search(r"(fetch_email_and_address|fetch_contact_preference)", text, re.I)
    if m:
        val = m.group(1).lower()
        return val if val in _ALLOWED else None
    return None

# ---------------------------
# Strands / Bedrock (Claude Sonnet 4)
# ---------------------------
def _classify_with_strands(query: str) -> Optional[str]:
    """
    Uses the Strands Agent with the Bedrock model id supplied.
    Requires:
      - pip install strands-agents
      - BEDROCK_MODEL_ID (default: us.anthropic.claude-sonnet-4-20250514-v1:0)
      - (Your environment configured so Strands can talk to Bedrock; e.g., AWS_BEARER_TOKEN_BEDROCK / project config)
    """
    model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
    

    try:
        agent = Agent(
            model=model_id,
            tools=[],                     # we only need a raw completion
            callback_handler=None,
            record_direct_tool_call=False,
        )
        # Most Strands builds accept a plain text prompt; if your SDK differs, adjust here.
        prompt = _SYSTEM_PROMPT + "\n\nUser query:\n" + (query or "")
        # Try common method names in order:
        text = None
        for meth in ("run", "complete", "generate", "invoke"):
            fn = getattr(agent, meth, None)
            if callable(fn):
                try:
                    res = fn(prompt)  # plain string prompt
                    text = str(res)
                    break
                except Exception:
                    continue
        if text is None:
            # Last resort: try a messages-style call if your SDK prefers that.
            run = getattr(agent, "run", None)
            if callable(run):
                res = run([{"role": "system", "content": _SYSTEM_PROMPT},
                           {"role": "user", "content": query or ""}])
                text = str(res)

        intent = _parse_intent(text or "")
        return intent
    except Exception as e:
        print(f"[intent-llm] strands classify failed: {e}")
        return None

def classify_intent_llm(query: str) -> str:
    """
    Orchestrates which LLM stack to use based on INTENT_LLM_STACK.
    Falls back to keywords if anything fails.
    """
    intent = _classify_with_strands(query)
    print(f"[intent-llm] strands classify: {intent}")
    return intent or classify_intent_keywords(query)
