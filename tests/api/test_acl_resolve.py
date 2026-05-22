"""Test ACL effective-role resolver (pure function, no DB)."""
import pytest

from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import ProjectRole, UserRole


@pytest.mark.parametrize(
    "global_role,is_owner,acl,expected",
    [
        (UserRole.MEMBER, True, None, ProjectRole.EDITOR),
        (UserRole.MEMBER, False, None, None),
        (UserRole.MEMBER, False, ProjectRole.VIEWER, ProjectRole.VIEWER),
        (UserRole.MEMBER, False, ProjectRole.EDITOR, ProjectRole.EDITOR),
        (UserRole.READER, False, None, ProjectRole.VIEWER),
        (UserRole.READER, False, ProjectRole.EDITOR, ProjectRole.EDITOR),
        (UserRole.OPERATOR, False, None, ProjectRole.EDITOR),
        (UserRole.OPERATOR, False, ProjectRole.VIEWER, ProjectRole.EDITOR),
        (UserRole.ADMIN, False, None, ProjectRole.EDITOR),
    ],
)
def test_resolve_matrix(
    global_role: UserRole,
    is_owner: bool,
    acl: ProjectRole | None,
    expected: ProjectRole | None,
) -> None:
    """Verify resolver matrix from decision D3b."""
    out = resolve_effective_project_role(global_role, is_owner=is_owner, acl_role=acl)
    assert out == expected
