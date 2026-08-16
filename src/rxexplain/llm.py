

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import AzureConfig, load_azure_config

MAX_RETRIES = 4
BASE_BACKOFF = 1.5


class LLMError(RuntimeError):
    """Raised when a call cannot be completed after retries."""


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    model: str = ""
    finish_reason: str = ""
    attempts: int = 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Usage:
    """Running token / call totals for one process."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    errors: int = 0

    def add(self, r: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.latency_s += r.latency_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_s": round(self.latency_s, 2),
            "errors": self.errors,
        }


@dataclass
class _Dialect:
    """What this deployment will actually accept."""

    token_param: str = "max_completion_tokens"
    supports_temperature: bool = True
    supports_json_mode: bool = True
    probed: bool = False
    fields: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Thin, retrying wrapper over one Azure chat deployment."""

    def __init__(self, cfg: AzureConfig | None = None) -> None:
        self.cfg = cfg or load_azure_config(require=True)
        assert self.cfg is not None
        self._client: Any | None = None
        self._dialect = _Dialect()
        self.usage = Usage()

    # ------------------------------------------------------------- plumbing
    @property
    def client(self) -> Any:
        if self._client is None:
            if self.cfg.mode == "openai_compat":
                from openai import OpenAI

                self._client = OpenAI(
                    base_url=self.cfg.compat_base_url,
                    api_key=self.cfg.api_key,
                    max_retries=0,      # we do our own, with logging
                )
            else:
                from openai import AzureOpenAI

                self._client = AzureOpenAI(
                    azure_endpoint=self.cfg.endpoint,
                    api_key=self.cfg.api_key,
                    api_version=self.cfg.api_version,
                    max_retries=0,
                )
        return self._client

    def describe(self) -> dict[str, str]:
        return {
            "mode": self.cfg.mode,
            "endpoint": self.cfg.endpoint,
            "resolved_base_url": (
                self.cfg.compat_base_url if self.cfg.mode == "openai_compat"
                else self.cfg.endpoint
            ),
            "deployment": self.cfg.deployment,
            "judge_deployment": self.cfg.judge_deployment,
            "api_version": self.cfg.api_version,
            "token_param": self._dialect.token_param,
            "temperature": str(self._dialect.supports_temperature),
        }

    # --------------------------------------------------------------- calling
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1200,
        temperature: float | None = 0.2,
        json_mode: bool = False,
        deployment: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion, adapting to the deployment's dialect."""
        model = deployment or self.cfg.deployment
        attempts = 0
        last_err: Exception | None = None
        budget = max_tokens

        while attempts < MAX_RETRIES:
            attempts += 1
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            kwargs[self._dialect.token_param] = budget
            if temperature is not None and self._dialect.supports_temperature:
                kwargs["temperature"] = temperature
            if json_mode and self._dialect.supports_json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            started = time.perf_counter()
            try:
                raw = self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_err = exc
                if self._adapt(exc):
                    attempts -= 1      # a dialect fix is not a real attempt
                    continue
                if self._retryable(exc) and attempts < MAX_RETRIES:
                    self.usage.errors += 1
                    time.sleep(BASE_BACKOFF ** attempts)
                    continue
                self.usage.errors += 1
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc

            elapsed = time.perf_counter() - started
            choice = raw.choices[0] if raw.choices else None
            text = ((choice.message.content if choice else None) or "").strip()
            finish = getattr(choice, "finish_reason", "") or ""
            usage = getattr(raw, "usage", None)
            resp = LLMResponse(
                text=text,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_s=elapsed,
                model=getattr(raw, "model", model) or model,
                finish_reason=finish,
                attempts=attempts,
            )
            self.usage.add(resp)

            # A reasoning model can burn the whole budget on hidden reasoning
            # tokens and return nothing. Retry once with a bigger allowance.
            if not text and finish == "length" and budget < 8000:
                budget = min(budget * 3, 8000)
                continue
            if not text and attempts < MAX_RETRIES:
                budget = min(max(budget * 2, 800), 8000)
                continue
            return resp

        raise LLMError(f"no usable response after {attempts} attempts: {last_err}")

    # ------------------------------------------------------------ adaptation
    def _adapt(self, exc: Exception) -> bool:
        """Learn the deployment's dialect from a 400. Returns True if changed."""
        msg = str(exc).lower()
        status = getattr(exc, "status_code", None)
        if status not in (400, 422) and "unsupported" not in msg and "unrecognized" not in msg:
            return False

        changed = False
        if ("max_tokens" in msg and "max_completion_tokens" in msg) or \
           ("unsupported parameter" in msg and "max_tokens" in msg):
            # the model is telling us which one it wants
            wants_completion = bool(
                re.search(r"use\s+'?max_completion_tokens", msg)
            ) or "max_tokens' is not supported" in msg
            new = "max_completion_tokens" if wants_completion else "max_tokens"
            if new != self._dialect.token_param:
                self._dialect.token_param = new
                changed = True
        elif "max_completion_tokens" in msg and "unrecognized" in msg:
            if self._dialect.token_param != "max_tokens":
                self._dialect.token_param = "max_tokens"
                changed = True
        elif "max_tokens" in msg and self._dialect.token_param == "max_tokens":
            self._dialect.token_param = "max_completion_tokens"
            changed = True

        if "temperature" in msg and self._dialect.supports_temperature:
            self._dialect.supports_temperature = False
            changed = True
        if "response_format" in msg and self._dialect.supports_json_mode:
            self._dialect.supports_json_mode = False
            changed = True
        return changed

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status in (408, 409, 425, 429, 500, 502, 503, 504):
            return True
        name = type(exc).__name__
        return name in (
            "RateLimitError", "APIConnectionError", "APITimeoutError",
            "InternalServerError", "APIStatusError",
        )

    # ------------------------------------------------------------- utilities
    def probe(self) -> dict[str, Any]:
        """Minimal round-trip, used by ``rxexplain doctor``."""
        started = time.perf_counter()
        resp = self.chat(
            [
                {"role": "system", "content": "Reply with exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=800,
            temperature=None,
        )
        return {
            "ok": bool(resp.text),
            "reply": resp.text[:80],
            "model": resp.model,
            "latency_s": round(time.perf_counter() - started, 2),
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "dialect": {
                "token_param": self._dialect.token_param,
                "supports_temperature": self._dialect.supports_temperature,
                "supports_json_mode": self._dialect.supports_json_mode,
            },
        }
