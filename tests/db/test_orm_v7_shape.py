"""Static (no-PG) shape test for the v7 ORM models.

Asserts the ORM exposes the new auth symbols and that ``User`` carries the
v7 column set.
"""
from __future__ import annotations


def test_user_has_v7_attributes() -> None:
    from flake_analysis.db.models import User

    cols = {c.name for c in User.__table__.columns}
    assert {"id", "cognito_sub", "email", "email_verified_at",
            "organization", "role", "deactivated_at"} <= cols


def test_enums_exist() -> None:
    from flake_analysis.db.models import ProjectRole, UserRole

    assert {"member", "reader", "operator", "admin"} == {r.value for r in UserRole}
    assert {"viewer", "editor"} == {r.value for r in ProjectRole}


def test_auth_models_importable() -> None:
    from flake_analysis.db.models import ProjectUser, UsageEvent

    assert ProjectUser.__tablename__ == "project_users"
    assert UsageEvent.__tablename__ == "usage_events"
