"""Answer cache. The key makes a cross-permission hit impossible by construction."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from app.auth.principal import Principal
from app.core.config import Settings


def normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def cache_key(
    question: str,
    principal: Principal,
    settings: Settings,
    *,
    kb_version: int,
    tool_registry_hash: str,
) -> str:
    """Permission scope and kb_version are part of the key — that is the whole point."""
    material = "|".join(
        [
            normalize_question(question),
            principal.tenant_id,
            principal.scope_hash(),
            str(kb_version),
            settings.prompt_version,
            settings.agent_version,
            tool_registry_hash,
            settings.euri_generation_model,
            "0.0",
            str(settings.retrieval_top_k),
            str(settings.relevance_threshold),
        ]
    )
    return "cache:answer:" + hashlib.sha256(material.encode()).hexdigest()


class AnswerCache:
    """Redis-backed when available; in-process otherwise. Never a source of truth."""

    def __init__(self, settings: Settings, redis_client: Any = None) -> None:
        self.settings = settings
        self.redis = redis_client
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        if self.redis is not None:
            raw = await self.redis.get(key)
            return json.loads(raw) if raw else None
        entry = self._memory.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires < time.time():
            self._memory.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: dict[str, Any]) -> None:
        ttl = self.settings.cache_ttl_seconds
        if self.redis is not None:
            await self.redis.setex(key, ttl, json.dumps(value, default=str))
        else:
            self._memory[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        self._memory.clear()
