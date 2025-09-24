from __future__ import annotations
from typing import Any, Dict, List
from collections.abc import Mapping

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
from app.utils.json_utils import (
    extract_first_email,
    extract_first_address,
    extract_preferences_list,
)

def _code(x: Any):
    return (x or {}).get("code") if isinstance(x, Mapping) else None

def build_email_address_output(
    member_id: str, email_json: Dict[str, Any], address_json: Dict[str, Any]
) -> ProfileOverviewResponse:
    email_first = extract_first_email(email_json) or {}
    addr_first = extract_first_address(address_json) or {}

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

def build_preferences_output(
    member_id: str, preferences_json: Dict[str, Any]
) -> PreferencesOverviewResponse:
    items = extract_preferences_list(preferences_json) or []
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
