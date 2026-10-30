from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GoogleLabelData(BaseModel):
    resource_name: str
    name: str


class GoogleContactLinkData(BaseModel):
    id: str
    account_email: str
    resource_name: str
    labels: list[GoogleLabelData]
    google_url: str | None = None
    can_write: bool


class GoogleContactDetailsData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    link_id: str
    birthdays: list[dict[str, Any]]
    biographies: list[dict[str, Any]]
    addresses: list[dict[str, Any]]
    urls: list[dict[str, Any]]
    user_defined: list[dict[str, Any]] = Field(alias="userDefined")


class GoogleContactReadData(BaseModel):
    gmail_enabled: bool
    links: list[GoogleContactLinkData]
    details: list[GoogleContactDetailsData]


class GoogleExportPreviewRequest(BaseModel):
    link_id: str


class GoogleExportChange(BaseModel):
    field: str
    label: str
    current: Any = None
    proposed: Any = None
    selected_by_default: bool = False


class GoogleExportPreviewData(BaseModel):
    preview_id: str
    account_email: str
    expires_at: datetime
    changes: list[GoogleExportChange]
    can_write: bool


class GoogleExportApplyRequest(BaseModel):
    preview_id: str
    fields: list[str]


class GoogleExportApplyData(BaseModel):
    status: str
    updated_fields: list[str]
