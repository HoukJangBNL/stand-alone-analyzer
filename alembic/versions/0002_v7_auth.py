"""v7 auth + ACL + usage_events

Revision ID: 0002_v7_auth
Revises: 0001_initial_v6
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_v7_auth"
down_revision: Union[str, None] = "0001_initial_v6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    raise NotImplementedError


def downgrade() -> None:
    raise NotImplementedError
