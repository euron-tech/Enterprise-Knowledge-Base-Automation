"""Tool registry.

Three guarantees are enforced here, in code, at registration and at dispatch:
  1. Every tool is read-only.
  2. No tool accepts a principal field as a model-supplied argument.
  3. Only registered tools, filtered by the caller's role, are ever dispatchable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.auth.principal import Principal, Role
from app.core.constants import PRINCIPAL_FIELDS

ToolFn = Callable[..., Awaitable[Any]]


class ToolError(Exception):
    """Typed, model-readable failure. Never a stack trace, never a partial run."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def as_result(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: ToolFn
    allowed_roles: frozenset[Role]
    cost_class: Literal["cheap", "moderate", "expensive"] = "cheap"
    max_calls_per_request: int = 4
    read_only: Literal[True] = True

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_schema.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.read_only is not True:
            raise ValueError(f"tool {spec.name} is not read-only; write tools are forbidden")

        # A model must never be able to name its own tenant, department, role or owner.
        fields = set(spec.args_schema.model_fields)
        leaked = fields & PRINCIPAL_FIELDS
        if leaked:
            raise ValueError(
                f"tool {spec.name} exposes principal field(s) {sorted(leaked)} as model arguments"
            )
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError("UNKNOWN_TOOL", f"tool {name!r} is not registered")
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def for_role(self, role: Role) -> list[ToolSpec]:
        return [s for s in self._tools.values() if role in s.allowed_roles]

    def schemas_for(self, role: Role) -> list[dict[str, Any]]:
        """What the model sees — filtered by role before exposure."""
        return [s.openai_schema() for s in self.for_role(role)]

    def validate_args(self, spec: ToolSpec, raw: dict[str, Any]) -> BaseModel:
        # Discard (and surface) any principal field the model tried to set.
        supplied = set(raw) & PRINCIPAL_FIELDS
        if supplied:
            raise ToolError(
                "PRINCIPAL_OVERRIDE",
                f"arguments {sorted(supplied)} are set by the server and cannot be supplied",
            )
        try:
            return spec.args_schema.model_validate(raw)
        except PydanticValidationError as exc:
            raise ToolError("INVALID_ARGUMENTS", str(exc.errors()[:3])) from exc

    async def dispatch(
        self, name: str, raw_args: dict[str, Any], principal: Principal, ctx: Any
    ) -> Any:
        spec = self.get(name)
        if principal.role not in spec.allowed_roles:
            raise ToolError("NOT_AUTHORIZED", f"role {principal.role!r} may not call {name!r}")
        args = self.validate_args(spec, raw_args)
        # Principal is injected here, server-side, from the verified JWT.
        return await spec.handler(args=args, principal=principal, ctx=ctx)


REGISTRY = ToolRegistry()
