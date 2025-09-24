from __future__ import annotations

def classify_intent_keywords(query: str) -> str:
    """
    Decide which tool to call based on a simple keyword check.
    Returns one of:
      - "fetch_email_and_address"
      - "fetch_contact_preference"
    """
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
