"""Multi-provider async LLM router (Ollama, OpenAI, Gemini, Anthropic, OpenAI-compatible) with telemetry & fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Literal, TypeVar

import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

from salespulsebi.ai.guardrails import redact_pii
from salespulsebi.config import Settings
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import AIProviderType, GlobalSettings

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "fast": "llama3.2",
        "reasoning": "qwen2.5:14b",
        "embedding": "nomic-embed-text",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "fast": "gpt-4o-mini",
        "reasoning": "gpt-4o",
        "embedding": "text-embedding-3-small",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "fast": "gemini-2.0-flash",
        "reasoning": "gemini-2.5-pro",
        "embedding": "text-embedding-004",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "fast": "claude-3-5-haiku-latest",
        "reasoning": "claude-3-5-sonnet-latest",
        "embedding": "",
    },
    "openai_compatible": {
        "base_url": "http://127.0.0.1:8000/v1",
        "fast": "local-model",
        "reasoning": "local-model",
        "embedding": "",
    },
}

# Approximate USD cost per 1M tokens (input, output) for exact Decimal telemetry accounting
MODEL_PRICING_PER_1M: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gemini-2.0-flash": (Decimal("0.10"), Decimal("0.40")),
    "gemini-2.5-pro": (Decimal("1.25"), Decimal("5.00")),
    "claude-3-5-haiku-latest": (Decimal("0.80"), Decimal("4.00")),
    "claude-3-5-sonnet-latest": (Decimal("3.00"), Decimal("15.00")),
}


def estimate_token_cost_usd(
    provider: str, model_id: str, input_tokens: int, output_tokens: int
) -> Decimal:
    if provider in ("disabled", "ollama"):
        return Decimal("0.000000")
    in_rate, out_rate = MODEL_PRICING_PER_1M.get(model_id, (Decimal("0.50"), Decimal("1.50")))
    with localcontext() as ctx:
        ctx.prec = 28
        million = Decimal("1000000")
        cost = (Decimal(input_tokens) * in_rate + Decimal(output_tokens) * out_rate) / million
        return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


@dataclass
class ResolvedAIConfig:
    provider: AIProviderType
    base_url: str
    api_key: str | None
    fast_model: str
    reasoning_model: str
    embedding_model: str
    redact_pii: bool
    monthly_budget_usd: Decimal
    ttft_timeout_s: float
    total_timeout_s: float
    max_output_tokens: int

    @property
    def is_enabled(self) -> bool:
        if self.provider == "disabled":
            return False
        if self.provider in ("openai", "gemini", "anthropic") and not self.api_key:
            return False
        return True

    def model_for_tier(self, tier: Literal["fast", "reasoning"]) -> str:
        return self.fast_model if tier == "fast" else self.reasoning_model


def resolve_ai_config(
    settings: Settings,
    global_settings: GlobalSettings | None = None,
) -> ResolvedAIConfig:
    """Merge runtime env Settings (with SecretStr API keys) and persisted UI GlobalSettings."""
    provider: AIProviderType = settings.ai_provider
    if global_settings and global_settings.ai_provider != "disabled":
        provider = global_settings.ai_provider
    elif global_settings and settings.ai_provider == "disabled":
        provider = global_settings.ai_provider

    defaults = DEFAULT_MODELS.get(provider, DEFAULT_MODELS["ollama"])
    base_url = (
        (global_settings.ai_base_url if global_settings and global_settings.ai_base_url else None)
        or settings.ai_base_url
        or defaults["base_url"]
    ).rstrip("/")

    fast_model = (
        (global_settings.ai_fast_model if global_settings and global_settings.ai_fast_model else "")
        or settings.ai_fast_model
        or defaults["fast"]
    )
    reasoning_model = (
        (
            global_settings.ai_reasoning_model
            if global_settings and global_settings.ai_reasoning_model
            else ""
        )
        or settings.ai_reasoning_model
        or defaults["reasoning"]
    )
    embedding_model = (
        (
            global_settings.ai_embedding_model
            if global_settings and global_settings.ai_embedding_model
            else ""
        )
        or settings.ai_embedding_model
        or defaults["embedding"]
    )

    raw_key = settings.ai_api_key
    api_key: str | None = (
        raw_key.get_secret_value()
        if hasattr(raw_key, "get_secret_value")
        else (str(raw_key) if raw_key else None)
    )
    if not api_key:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai_compatible": "OPENAI_API_KEY",
        }
        env_name = env_map.get(provider)
        if env_name:
            api_key = os.getenv(env_name) or None

    return ResolvedAIConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        fast_model=fast_model,
        reasoning_model=reasoning_model,
        embedding_model=embedding_model,
        redact_pii=global_settings.ai_redact_pii if global_settings else True,
        monthly_budget_usd=(
            global_settings.ai_monthly_budget_usd if global_settings else Decimal("25.00")
        ),
        ttft_timeout_s=settings.ai_ttft_timeout_s,
        total_timeout_s=settings.ai_total_timeout_s,
        max_output_tokens=settings.ai_max_output_tokens,
    )


@dataclass
class LLMCallResult:
    ok: bool
    text: str
    provider: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    error: str | None = None
    raw_tool_calls: list[dict[str, Any]] | None = None


def deterministic_local_embedding(text: str, dim: int = 128) -> list[float]:
    """
    Deterministic, zero-dependency local semantic-lexical hashing embedding (character 3-grams + words).
    Used when no external embedding model is configured so DuckDB local RAG always works 100% offline.
    """
    vec = np.zeros(dim, dtype=np.float32)
    cleaned = text.lower().strip()
    if not cleaned:
        return vec.tolist()
    tokens = [tok for tok in cleaned.split() if len(tok) >= 2]
    features: list[str] = list(tokens)
    for tok in tokens[:250]:
        if len(tok) >= 4:
            for i in range(len(tok) - 2):
                features.append(tok[i : i + 3])
    for feat in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 1e-9:
        vec /= norm
    return [round(float(x), 6) for x in vec]


class MultiProviderLLMRouter:
    """Unified multi-provider LLM client with tiered routing, retries, JSON schema validation, and telemetry."""

    def __init__(
        self,
        settings: Settings,
        db: MetadataDB,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._transport = transport

    def get_config(self) -> ResolvedAIConfig:
        return resolve_ai_config(self.settings, self.db.get_global_settings())

    def get_public_status(self) -> dict[str, Any]:
        cfg = self.get_config()
        return {
            "provider": cfg.provider,
            "enabled": cfg.is_enabled,
            "mode": "local" if cfg.provider in ("disabled", "ollama") else "cloud",
            "base_url": cfg.base_url,
            "fast_model": cfg.fast_model,
            "reasoning_model": cfg.reasoning_model,
            "embedding_model": cfg.embedding_model,
            "api_key_configured": bool(cfg.api_key),
            "redact_pii": cfg.redact_pii,
            "monthly_budget_usd": format(cfg.monthly_budget_usd, "f"),
            "supported_providers": [
                "disabled",
                "ollama",
                "openai",
                "gemini",
                "anthropic",
                "openai_compatible",
            ],
        }

    def _build_client(self, timeout_s: float) -> httpx.AsyncClient:
        timeout = httpx.Timeout(timeout_s, connect=min(6.0, timeout_s))
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def _post_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_s: float,
        max_attempts: int = 3,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        async with self._build_client(timeout_s) as client:
            for attempt in range(max_attempts):
                try:
                    resp = await client.post(url, headers=headers, json=json_body)
                    if resp.status_code in (429, 502, 503, 504) and attempt + 1 < max_attempts:
                        await asyncio.sleep((0.4 * (2**attempt)) + random.uniform(0.05, 0.25))
                        continue
                    resp.raise_for_status()
                    return resp
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429:
                        raise
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep((0.4 * (2**attempt)) + random.uniform(0.05, 0.2))
                        continue
                    raise
        assert last_exc is not None
        raise last_exc

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tier: Literal["fast", "reasoning"] = "reasoning",
        json_schema: dict[str, Any] | None = None,
        project_id: str | None = None,
        feature_name: str = "copilot",
        prompt_version: str = "v1",
        trace_id: str | None = None,
        temperature: float = 0.1,
    ) -> LLMCallResult:
        cfg = self.get_config()
        tid = trace_id or str(uuid.uuid4())
        if not cfg.is_enabled:
            return LLMCallResult(
                ok=False,
                text="",
                provider=cfg.provider,
                model_id="deterministic_fallback",
                error="ai_provider_disabled_or_missing_key",
            )

        model_id = cfg.model_for_tier(tier)
        send_user = redact_pii(user_prompt) if (cfg.redact_pii and cfg.provider != "ollama") else user_prompt
        t0 = time.perf_counter()

        try:
            text, in_tok, out_tok = await self._dispatch_provider(
                cfg=cfg,
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=send_user,
                json_schema=json_schema,
                temperature=temperature,
            )
            lat_ms = int((time.perf_counter() - t0) * 1000)
            cost = estimate_token_cost_usd(cfg.provider, model_id, in_tok, out_tok)
            self.db.record_ai_span(
                trace_id=tid,
                project_id=project_id,
                feature_name=feature_name,
                prompt_version=prompt_version,
                provider=cfg.provider,
                model_id=model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=cost,
                latency_ms=lat_ms,
                ttft_ms=lat_ms,
                status="ok",
            )
            return LLMCallResult(
                ok=True,
                text=text,
                provider=cfg.provider,
                model_id=model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=lat_ms,
                estimated_cost_usd=cost,
            )
        except Exception as exc:
            lat_ms = int((time.perf_counter() - t0) * 1000)
            self.db.record_ai_span(
                trace_id=tid,
                project_id=project_id,
                feature_name=feature_name,
                prompt_version=prompt_version,
                provider=cfg.provider,
                model_id=model_id,
                latency_ms=lat_ms,
                status="error",
            )
            return LLMCallResult(
                ok=False,
                text="",
                provider=cfg.provider,
                model_id=model_id,
                latency_ms=lat_ms,
                error=type(exc).__name__,
            )

    async def complete_structured(
        self,
        *,
        schema_cls: type[TModel],
        system_prompt: str,
        user_prompt: str,
        tier: Literal["fast", "reasoning"] = "fast",
        project_id: str | None = None,
        feature_name: str = "structured",
        prompt_version: str = "v1",
        max_validation_retries: int = 2,
    ) -> tuple[TModel | None, LLMCallResult]:
        """Enforce Pydantic v2 structured output with bounded self-correction retries."""
        schema_dict = schema_cls.model_json_schema()
        current_prompt = user_prompt
        last_res = LLMCallResult(ok=False, text="", provider="disabled", model_id="none")
        tid = str(uuid.uuid4())

        for attempt in range(max_validation_retries + 1):
            res = await self.complete(
                system_prompt=system_prompt,
                user_prompt=current_prompt,
                tier=tier,
                json_schema=schema_dict,
                project_id=project_id,
                feature_name=feature_name,
                prompt_version=prompt_version,
                trace_id=tid,
                temperature=0.0,
            )
            last_res = res
            if not res.ok:
                return None, res

            raw = res.text.strip()
            # Strip accidental markdown code fences if a provider ignores JSON mode
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            try:
                parsed = schema_cls.model_validate_json(raw)
                return parsed, res
            except ValidationError as exc:
                if attempt >= max_validation_retries:
                    return None, LLMCallResult(
                        ok=False,
                        text=res.text,
                        provider=res.provider,
                        model_id=res.model_id,
                        latency_ms=res.latency_ms,
                        error="schema_validation_failed",
                    )
                current_prompt = (
                    f"{user_prompt}\n\n"
                    f"Previous JSON failed Pydantic validation with errors:\n{exc.errors()[:4]}\n"
                    f"Return ONLY valid JSON matching the exact schema."
                )

        return None, last_res

    async def _dispatch_provider(
        self,
        *,
        cfg: ResolvedAIConfig,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None,
        temperature: float,
    ) -> tuple[str, int, int]:
        if cfg.provider == "ollama":
            body: dict[str, Any] = {
                "model": model_id,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": cfg.max_output_tokens},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if json_schema is not None:
                body["format"] = json_schema
            resp = await self._post_with_retry(
                f"{cfg.base_url}/api/chat",
                headers={"Content-Type": "application/json"},
                json_body=body,
                timeout_s=cfg.total_timeout_s,
            )
            data = resp.json()
            text = str(data.get("message", {}).get("content", ""))
            return text, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)

        if cfg.provider in ("openai", "openai_compatible"):
            headers = {"Content-Type": "application/json"}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"
            body = {
                "model": model_id,
                "temperature": temperature,
                "max_tokens": cfg.max_output_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if json_schema is not None:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "salespulse_output", "schema": json_schema, "strict": False},
                }
            resp = await self._post_with_retry(
                f"{cfg.base_url}/chat/completions",
                headers=headers,
                json_body=body,
                timeout_s=cfg.total_timeout_s,
            )
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            text = str(choice.get("message", {}).get("content") or "")
            usage = data.get("usage") or {}
            return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

        if cfg.provider == "gemini":
            url = f"{cfg.base_url}/models/{model_id}:generateContent?key={cfg.api_key}"
            gen_cfg: dict[str, Any] = {
                "temperature": temperature,
                "maxOutputTokens": cfg.max_output_tokens,
            }
            if json_schema is not None:
                gen_cfg["responseMimeType"] = "application/json"
            body = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": gen_cfg,
            }
            resp = await self._post_with_retry(
                url,
                headers={"Content-Type": "application/json"},
                json_body=body,
                timeout_s=cfg.total_timeout_s,
            )
            data = resp.json()
            cands = data.get("candidates") or [{}]
            parts = cands[0].get("content", {}).get("parts") or [{}]
            text = "".join(str(p.get("text") or "") for p in parts)
            usage = data.get("usageMetadata") or {}
            return (
                text,
                int(usage.get("promptTokenCount") or 0),
                int(usage.get("candidatesTokenCount") or 0),
            )

        if cfg.provider == "anthropic":
            headers = {
                "Content-Type": "application/json",
                "x-api-key": cfg.api_key or "",
                "anthropic-version": "2023-06-01",
            }
            sys_txt = system_prompt
            if json_schema is not None:
                sys_txt += f"\n\nReturn ONLY raw JSON conforming to:\n{json.dumps(json_schema)}"
            body = {
                "model": model_id,
                "max_tokens": cfg.max_output_tokens,
                "temperature": temperature,
                "system": sys_txt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            resp = await self._post_with_retry(
                f"{cfg.base_url}/messages",
                headers=headers,
                json_body=body,
                timeout_s=cfg.total_timeout_s,
            )
            data = resp.json()
            blocks = data.get("content") or []
            text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
            usage = data.get("usage") or {}
            return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)

        raise ValueError(f"Unsupported provider: {cfg.provider}")

    async def embed_texts(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """Generate vector embeddings via configured provider or deterministic local fallback."""
        cfg = self.get_config()
        if not texts:
            return [], "none"
        if cfg.is_enabled and cfg.embedding_model:
            try:
                if cfg.provider == "ollama":
                    vectors: list[list[float]] = []
                    for txt in texts:
                        resp = await self._post_with_retry(
                            f"{cfg.base_url}/api/embeddings",
                            headers={"Content-Type": "application/json"},
                            json_body={"model": cfg.embedding_model, "prompt": txt[:4000]},
                            timeout_s=15.0,
                            max_attempts=2,
                        )
                        emb = resp.json().get("embedding")
                        if not emb:
                            raise ValueError("empty_embedding")
                        vectors.append([float(x) for x in emb])
                    return vectors, f"ollama:{cfg.embedding_model}"
                if cfg.provider in ("openai", "openai_compatible"):
                    headers = {"Content-Type": "application/json"}
                    if cfg.api_key:
                        headers["Authorization"] = f"Bearer {cfg.api_key}"
                    resp = await self._post_with_retry(
                        f"{cfg.base_url}/embeddings",
                        headers=headers,
                        json_body={"model": cfg.embedding_model, "input": [t[:4000] for t in texts]},
                        timeout_s=15.0,
                        max_attempts=2,
                    )
                    data_items = resp.json().get("data") or []
                    if len(data_items) == len(texts):
                        return (
                            [[float(x) for x in item["embedding"]] for item in data_items],
                            f"{cfg.provider}:{cfg.embedding_model}",
                        )
            except Exception:
                pass

        return [deterministic_local_embedding(t) for t in texts], "local_hash_128d"

    async def test_connection(
        self,
        *,
        provider: AIProviderType | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Test connectivity to the selected LLM provider safely without leaking secrets."""
        cfg = self.get_config()
        eff_provider = provider or cfg.provider
        if eff_provider == "disabled":
            return {
                "ok": True,
                "provider": "disabled",
                "mode": "deterministic_local",
                "message": "Deterministic local analytics mode active (no external LLM required).",
            }

        defaults = DEFAULT_MODELS.get(eff_provider, DEFAULT_MODELS["ollama"])
        eff_url = (base_url or cfg.base_url or defaults["base_url"]).rstrip("/")
        eff_model = model or cfg.fast_model or defaults["fast"]

        if eff_provider == "ollama":
            try:
                async with self._build_client(6.0) as client:
                    resp = await client.get(f"{eff_url}/api/tags")
                    resp.raise_for_status()
                    models = [m.get("name") for m in resp.json().get("models", [])]
                return {
                    "ok": True,
                    "provider": "ollama",
                    "models_found": models[:20],
                    "message": f"Connected to Ollama ({len(models)} models available).",
                }
            except Exception as exc:
                return {"ok": False, "provider": "ollama", "message": f"Ollama unreachable: {type(exc).__name__}"}

        if not cfg.api_key:
            return {
                "ok": False,
                "provider": eff_provider,
                "message": f"API key not found in environment for {eff_provider}.",
            }

        res = await self.complete(
            system_prompt="Reply with the single word OK.",
            user_prompt="Health check",
            tier="fast",
            feature_name="connection_test",
        )
        return {
            "ok": res.ok,
            "provider": eff_provider,
            "model": eff_model,
            "latency_ms": res.latency_ms,
            "message": "Connection verified." if res.ok else f"Connection failed: {res.error}",
        }
