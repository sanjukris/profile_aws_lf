from __future__ import annotations
import os
from app.utils.intent_keywords import classify_intent_keywords
from app.utils.intent_llm import classify_intent_llm

def classify_intent(query: str) -> str:
    """
    Dynamic classifier controlled by env:
      INTENT_CLASSIFIER=keywords | llm   (default: keywords)
      INTENT_LLM_STACK=langgraph | strands (default: langgraph)  # only used when INTENT_CLASSIFIER=llm
    """
    mode = (os.getenv("INTENT_CLASSIFIER") or "keywords").strip().lower()
    if mode == "llm":
        print(f"[intent] using llm: {mode}")
        return classify_intent_llm(query)
    print(f"[intent] using keywords: {mode}")
    return classify_intent_keywords(query)
