"""THE TENANCY CHOKEPOINT.

Every vector query in this system is built here. There is no other path.
A filter without a tenant_id is not a bug to be caught in review — it raises.
"""

from __future__ import annotations

from typing import Any

from qdrant_client.http import models as qm

from app.auth.principal import Principal
from app.core.errors import TenancyError


def build_filter(
    principal: Principal,
    *,
    department: str | None = None,
    document_ids: list[str] | None = None,
    include_retired: bool = False,
) -> qm.Filter:
    """Build the only vector filter this system will ever issue.

    Fails closed if tenant scope is missing or if the caller asks for a department
    they do not hold.
    """
    if not principal or not principal.tenant_id or not str(principal.tenant_id).strip():
        raise TenancyError("refusing to build a vector filter without a tenant_id")

    must: list[Any] = [
        qm.FieldCondition(
            key="tenant_id", match=qm.MatchValue(value=principal.tenant_id)
        )
    ]

    if department is not None:
        if not principal.may_access(department):
            raise TenancyError(f"principal may not access department {department!r}")
        must.append(
            qm.FieldCondition(key="department", match=qm.MatchValue(value=department))
        )
    elif not principal.is_admin:
        # Non-admins are always confined to their granted departments.
        if not principal.departments:
            raise TenancyError("principal has no department grants")
        must.append(
            qm.FieldCondition(
                key="department", match=qm.MatchAny(any=list(principal.departments))
            )
        )

    if not include_retired:
        must.append(
            qm.FieldCondition(key="status", match=qm.MatchValue(value="active"))
        )

    if document_ids:
        must.append(
            qm.FieldCondition(key="document_id", match=qm.MatchAny(any=document_ids))
        )

    return qm.Filter(must=must)


def assert_tenant_scoped(flt: qm.Filter, tenant_id: str) -> None:
    """Defence in depth: verify a filter really carries the tenant before it is used."""
    conditions = list(flt.must or [])
    for c in conditions:
        key = getattr(c, "key", None)
        match = getattr(c, "match", None)
        if key == "tenant_id" and getattr(match, "value", None) == tenant_id:
            return
    raise TenancyError("filter is not tenant-scoped")
