from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class NameValue(BaseModel):
    name: str
    value: Optional[Any] = None

class Journey(BaseModel):
    journey: str
    subjourney: str
    task: str
    subtask: str


class Header(BaseModel):
    title: str
    description: Optional[str] = None


class EntitiesEmailAddr(BaseModel):
    name: str
    value: Optional[str] = None


class EmailAddressBlock(BaseModel):
    email: List[NameValue]
    address: List[NameValue]


class ProfileOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_journey: Journey
    header: Header
    entities: List[EntitiesEmailAddr]
    data: EmailAddressBlock


class ContactMethod(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contactTypeCd: Dict[str, Any]
    contactUid: Optional[str] = None


class PreferenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    preferenceUid: str
    preferenceTypeCd: Dict[str, Any]
    preferenceValueCd: Optional[Dict[str, Any]] = None
    contactMethod: Optional[List[ContactMethod]] = None
    defaulted: Optional[str] = None
    clearSelection: Optional[str] = None
    allowClearSelection: Optional[str] = None
    terminationDt: Optional[str] = None
    effectiveDt: Optional[str] = None
    lastUpdatedChannel: Optional[Dict[str, Any]] = None
    lastUpdatedBy: Optional[str] = None
    lastUpdTimeStamp: Optional[str] = None
    origin: Optional[Dict[str, Any]] = None
    altIndicator: Optional[bool] = None


class PreferencesData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    preferences: List[PreferenceItem]


class PreferencesOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_journey: Journey
    header: Header
    entities: List[EntitiesEmailAddr]
    data: PreferencesData