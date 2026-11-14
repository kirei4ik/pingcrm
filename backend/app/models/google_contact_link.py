"""Account-scoped provenance for imported Google Contacts."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GoogleContactLink(Base):
    __tablename__ = "google_contact_links"
    __table_args__ = (UniqueConstraint("google_account_id", "resource_name", name="uq_google_contact_link_account_resource"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    google_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_name: Mapped[str] = mapped_column(String, nullable=False)
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    labels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    last_imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
