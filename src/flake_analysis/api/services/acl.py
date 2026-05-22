"""ACL effective-role resolver per decision D3b.

Pure function: no DB calls, no side effects. Receives global role, ownership
status, and optional ACL row; returns effective ProjectRole or None.
"""
from __future__ import annotations

from flake_analysis.db.models import ProjectRole, UserRole


def resolve_effective_project_role(
    global_role: UserRole,
    *,
    is_owner: bool,
    acl_role: ProjectRole | None,
) -> ProjectRole | None:
    """Compute effective project-level role from global role + ownership + ACL.

    Decision D3b matrix:
    - admin / operator → ALWAYS editor (never demoted)
    - Owner → EDITOR regardless of global role
    - reader → viewer baseline (can be upgraded by ACL)
    - member (non-owner) → requires ACL row; None if missing

    Args:
        global_role: User's global UserRole (member/reader/operator/admin)
        is_owner: True when user.id == project.created_by_id
        acl_role: Optional project_users.project_role (viewer/editor)

    Returns:
        ProjectRole.EDITOR, ProjectRole.VIEWER, or None (no access)
    """
    # Shortcut: admin and operator always get editor
    if global_role in (UserRole.ADMIN, UserRole.OPERATOR):
        return ProjectRole.EDITOR

    # Owner gets editor regardless of global role
    if is_owner:
        return ProjectRole.EDITOR

    # reader baseline: viewer, can be upgraded by ACL
    if global_role == UserRole.READER:
        if acl_role == ProjectRole.EDITOR:
            return ProjectRole.EDITOR
        return ProjectRole.VIEWER

    # member (non-owner): requires ACL
    if global_role == UserRole.MEMBER:
        return acl_role  # None if no ACL row

    # Defensive: should be unreachable given enum exhaustion
    return None
