"""The verified caller. Everything downstream takes this, never raw claims."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["user", "admin"]


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    role: Role
    departments: tuple[str, ...]
    email: str = ""
    correlation_id: str = "-"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may_access(self, department: str) -> bool:
        return self.is_admin or department in self.departments

    def scope_hash(self) -> str:
        """Stable hash of the permission scope — part of every cache key."""
        import hashlib

        raw = f"{self.tenant_id}|{self.role}|{'|'.join(sorted(self.departments))}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
