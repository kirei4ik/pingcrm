"""Store account-scoped Google Contact snapshots and granted scopes."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "4f6a7b8c9d01"
down_revision: Union[str, Sequence[str], None] = (
    "5fcb77520fec",
    "e0f1a2b3c4d5",
    "e2f3a4b5c6d7",
    "d6e7f8a1b2c3",
    "b2c3d4e5f6a8",
    "d5e6f7a8b9c0",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("google_accounts", sa.Column("granted_scopes", postgresql.JSONB(), nullable=True))
    op.create_table(
        "google_contact_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_name", sa.String(), nullable=False),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("google_account_id", "resource_name", name="uq_google_contact_link_account_resource"),
    )
    op.create_index("ix_google_contact_links_contact_id", "google_contact_links", ["contact_id"])
    op.create_index("ix_google_contact_links_google_account_id", "google_contact_links", ["google_account_id"])


def downgrade() -> None:
    op.drop_index("ix_google_contact_links_google_account_id", table_name="google_contact_links")
    op.drop_index("ix_google_contact_links_contact_id", table_name="google_contact_links")
    op.drop_table("google_contact_links")
    op.drop_column("google_accounts", "granted_scopes")
