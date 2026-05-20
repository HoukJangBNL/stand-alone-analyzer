"""Auth stub per backend design §4."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class User:
    """Minimal user identity. v1 stub returns (id='local', roles=('owner',))."""
    id: str
    roles: tuple[str, ...]

async def get_current_user() -> User:
    """v1 stub. Post-v1: parse Authorization header, validate JWT/SSO, raise 401 on failure."""
    return User(id="local", roles=("owner",))
