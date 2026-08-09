"""Euri AI Gateway client.

Every quirk encoded here was found by probing the live API, not by reading docs.
See docs/INTEGRATIONS-EURI.md.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import UpstreamError, UpstreamPermanentError, ValidationError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

# Verified: BatchEmbedContentsRequest allows at most 100 items.
MAX_EMBED_BATCH = 100
# Verified: gemini-embedding accepts 128..3072 output dimensions; 4096 is rejected.
MAX_DIMENSIONS = 3072
MAX_RETRIES = 3


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    raw_message: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        # Verified: finish_reason is "stop" even when tool_calls are present under a
        # forced tool_choice. Presence is the only reliable signal.
        return bool(self.tool_calls)


class EuriClient:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self._client = client
        self._price_cache: dict[str, dict[str, float]] = {}
        self._price_fetched_at = 0.0

    # ---------------------------------------------------------------- plumbing
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.euri_base_url,
                headers={
                    "Authorization": f"Bearer {self.settings.euri_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0),
                # Stateless: the gateway sets an AWSALB cookie we must never share.
                cookies=None,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _classify(response: httpx.Response) -> None:
        """The gateway wraps upstream 4xx as HTTP 500. A 500 mentioning 400 is permanent."""
        if response.is_success:
            return
        body = response.text[:400]
        status = response.status_code
        if status in (400, 401, 403, 404, 422):
            raise UpstreamPermanentError(f"gateway {status}: {body}")
        if status == 429:
            raise UpstreamError(f"gateway rate limited: {body}")
        if status >= 500:
            lowered = body.lower()
            if (
                '"400' in lowered
                or "invalid argument" in lowered
                or "invalid model" in lowered
            ):
                raise UpstreamPermanentError(
                    f"gateway {status} wrapping upstream 4xx: {body}"
                )
            raise UpstreamError(f"gateway {status}: {body}")
        raise UpstreamError(f"gateway {status}: {body}")

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._http().post(path, json=payload)
                self._classify(resp)
                return resp.json()
            except UpstreamPermanentError:
                raise  # never retry — retrying an invalid request only burns budget
            except (UpstreamError, httpx.HTTPError) as exc:
                last = exc
                if attempt == MAX_RETRIES - 1:
                    break
                backoff = (2**attempt) * 0.5 + random.uniform(0, 0.3)  # noqa: S311
                await asyncio.sleep(backoff)
        raise UpstreamError(f"{path} failed after {MAX_RETRIES} attempts: {last}")

    # ---------------------------------------------------------------- pricing
    async def prices(self) -> dict[str, dict[str, float]]:
        """Prices come from GET /models. Never hard-coded."""
        if self._price_cache and time.time() - self._price_fetched_at < 3600:
            return self._price_cache
        try:
            resp = await self._http().get("/models")
            self._classify(resp)
            for m in resp.json().get("data", []):
                p = m.get("pricing") or {}
                self._price_cache[m["id"]] = {
                    "input": float(p.get("inputPriceUsdPerMillion") or 0.0),
                    "output": float(p.get("outputPriceUsdPerMillion") or 0.0),
                }
            self._price_fetched_at = time.time()
        except Exception as exc:  # noqa: BLE001 - pricing must not break a request
            log_event(
                logger, logging.WARNING, "euri.price_fetch_failed", error=str(exc)
            )
        return self._price_cache

    async def estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        prices = await self.prices()
        p = prices.get(model, {"input": 0.0, "output": 0.0})
        return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000

    # ---------------------------------------------------------------- embeddings
    async def embed(
        self, texts: list[str], *, dimensions: int | None = None
    ) -> list[list[float]]:
        """Embed text. TEXT ONLY — see the guard below and INTEGRATIONS-EURI.md §3."""
        if not texts:
            return []
        dims = min(
            dimensions or self.settings.euri_embedding_dimensions, MAX_DIMENSIONS
        )

        for t in texts:
            if not isinstance(t, str):
                raise ValidationError("embedding input must be str")
            # Verified silent-failure mode: a data-URI embeds the *base64 text*, producing
            # a plausible-looking but meaningless vector. Refuse rather than corrupt the index.
            if t.startswith("data:") and ";base64," in t[:64]:
                raise ValidationError(
                    "refusing to embed a data URI as text — the gateway would embed the "
                    "base64 string, not the media. Bridge media to text first."
                )

        out: list[list[float]] = []
        for i in range(0, len(texts), MAX_EMBED_BATCH):
            batch = texts[i : i + MAX_EMBED_BATCH]
            data = await self._post(
                "/embeddings",
                {
                    "model": self.settings.euri_embedding_model,
                    "input": batch,
                    "dimensions": dims,
                },
            )
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            out.extend(item["embedding"] for item in items)
        return out

    async def embed_one(
        self, text: str, *, dimensions: int | None = None
    ) -> list[float]:
        vectors = await self.embed([text], dimensions=dimensions)
        return vectors[0]

    # ---------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model or self.settings.euri_generation_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format

        data = await self._post("/chat/completions", payload)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        return ChatResult(
            content=msg.get("content"),
            tool_calls=msg.get("tool_calls") or [],
            model=data.get("model", payload["model"]),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason", ""),
            raw_message=msg,
        )

    async def describe_image(self, image_b64: str, mime: str = "image/png") -> str:
        """The text bridge for images: vision describes, then the description is embedded."""
        result = await self.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this image for a search index. State the type "
                                "(diagram, chart, screenshot, photo), all visible text, and "
                                "what it depicts. Be factual and concise."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                    ],
                }
            ],
            model=self.settings.euri_vision_model,
            max_tokens=400,
        )
        return result.content or ""

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        """The text bridge for audio. Verified working on /audio/transcriptions."""
        client = self._http()
        resp = await client.post(
            "/audio/transcriptions",
            files={"file": (filename, audio, "application/octet-stream")},
            data={"model": self.settings.euri_transcribe_model},
            headers={
                "Authorization": f"Bearer {self.settings.euri_api_key.get_secret_value()}"
            },
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=120.0, pool=5.0),
        )
        self._classify(resp)
        body = resp.json()
        return body.get("text") or body.get("transcript") or ""
