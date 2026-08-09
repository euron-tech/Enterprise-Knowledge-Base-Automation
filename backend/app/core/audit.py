"""Append-only audit trail. Writes here never fail the request they describe."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import correlation_id_var, get_logger, log_event
from app.db.models import AuditEvent
from app.db.session import get_sessionmaker

logger = get_logger(__name__)


class Actions:
    AUTH_FAILURE = "auth.failure"
    AUTHZ_DENIED = "authz.denied"
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DELETE = "document.delete"
    INJECTION_DETECTED = "guardrail.injection_detected"
    GUARDRAIL_BLOCK = "guardrail.block"
    PRINCIPAL_OVERRIDE_ATTEMPT = "agent.principal_override_attempt"
    UNREGISTERED_TOOL = "agent.unregistered_tool"
    BUDGET_EXCEEDED = "agent.budget_exceeded"
    ADMIN_METRICS = "admin.metrics_access"
    RATE_LIMITED = "api.rate_limited"


async def record(
    action: str,
    *,
    tenant_id: str = "-",
    actor_id: str = "-",
    resource_type: str = "",
    resource_id: str = "",
    outcome: str = "denied",
    ip: str = "",
    session: AsyncSession | None = None,
    **metadata: Any,
) -> None:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        correlation_id=correlation_id_var.get(),
        ip=ip,
        metadata_json=metadata,
    )
    log_event(
        logger,
        logging.WARNING if outcome == "denied" else logging.INFO,
        f"audit.{action}",
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        **metadata,
    )
    try:
        if session is not None:
            session.add(event)
            await session.flush()
        else:
            async with get_sessionmaker()() as s:
                s.add(event)
                await s.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must never break the request
        log_event(
            logger, logging.ERROR, "audit.write_failed", error=str(exc), action=action
        )
