from __future__ import annotations

import json
import logging
import socket
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from margin_inference.secrets import resolve_secret
from margin_inference.types import (
    InferencePolicyError,
    InferenceResponse,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InferenceUsage,
)

logger = logging.getLogger(__name__)

_OPENER = build_opener(ProxyHandler({}))


def chat_completions_url(endpoint_url: str) -> str:
    normalized = endpoint_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def is_http_endpoint(endpoint_url: str | None) -> bool:
    if endpoint_url is None:
        return False
    parsed = urlparse(endpoint_url.strip())
    return parsed.scheme in {"http", "https"} and parsed.hostname is not None


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _parse_usage(body: dict[str, Any], payload: dict[str, Any], content: str) -> InferenceUsage:
    usage = body.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            tot = total_tokens if isinstance(total_tokens, int) else (prompt_tokens + completion_tokens)
            return InferenceUsage(
                prompt_tokens=max(0, prompt_tokens),
                completion_tokens=max(0, completion_tokens),
                total_tokens=max(0, tot),
            )
    est_prompt = _estimate_tokens(json.dumps(payload, ensure_ascii=False))
    est_completion = _estimate_tokens(content)
    return InferenceUsage(
        prompt_tokens=est_prompt, completion_tokens=est_completion, total_tokens=est_prompt + est_completion
    )


def _log_interaction(
    *,
    event: str,
    base_url: str,
    model: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    start: datetime,
    error: str | None = None,
    redact_content: bool = True,
) -> None:
    end = datetime.now(UTC)
    safe_request = request_payload
    safe_response = response_payload
    if redact_content:
        safe_request = {
            "redacted": True,
            "message_count": len(request_payload.get("messages", [])),
            "max_tokens": request_payload.get("max_tokens"),
        }
        safe_response = {"redacted": True, "received": response_payload is not None}
    payload = {
        "event": event,
        "base_url": base_url,
        "model": model,
        "request": safe_request,
        "response": safe_response,
        "error": error,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "llm_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"llm_interaction": payload},
    )


class InferenceClient:
    """Generic HTTP/OpenAI-compatible inference client."""

    def __init__(
        self, base_url: str, model: str, secret_reference: str | None = None, timeout_seconds: int = 120
    ) -> None:
        if not is_http_endpoint(base_url):
            raise InferencePolicyError("inference endpoint must be an HTTP(S) URL")
        self.base_url = base_url.strip()
        self.model = model.strip()
        self.secret_reference = secret_reference
        self.timeout_seconds = timeout_seconds

    def probe(self, timeout_seconds: int = 5) -> tuple[str, int, str]:
        """Health probe endpoint.

        Returns (status, latency_ms, detail).
        """
        start = time.monotonic()
        endpoint = self.base_url.rstrip("/")
        models_endpoint = endpoint if endpoint.endswith("/models") else f"{endpoint}/models"
        try:
            secret = resolve_secret(self.secret_reference)
        except InferencePolicyError as exc:
            return "failed", 0, str(exc)
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        req = Request(models_endpoint, headers=headers, method="GET")
        models_endpoint_responded = False
        try:
            with _OPENER.open(req, timeout=timeout_seconds) as response:
                body = response.read(20_000).decode("utf-8", errors="replace")
                status_code = response.status
                latency_ms = int((time.monotonic() - start) * 1000)
                if 200 <= status_code < 300:
                    models_endpoint_responded = True
                    try:
                        payload = json.loads(body) if body else {}
                        ids = []
                        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                            ids = [str(item.get("id", "")) for item in payload["data"] if isinstance(item, dict)]
                        if ids and self.model and self.model not in ids:
                            return "failed", latency_ms, "configured model was not listed by provider"
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

        try:
            self.chat_completions(
                messages=[{"role": "system", "content": "Return one word."}, {"role": "user", "content": "ok"}],
                max_tokens=1,
                temperature=0.0,
                timeout_seconds=timeout_seconds,
                event="probe",
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            detail = (
                "provider /models and chat endpoints responded"
                if models_endpoint_responded
                else "inference endpoint responded"
            )
            return "healthy", latency_ms, detail
        except InferenceTimeoutError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return "failed", latency_ms, f"health probe timed out: {exc}"
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return "failed", latency_ms, str(exc)

    def chat_completions(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 0,
        timeout_seconds: int | None = None,
        event: str = "chat",
        redact_content: bool = True,
        response_format: dict[str, Any] | None = None,
    ) -> InferenceResponse:
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        payload: dict[str, Any] = {"model": self.model, "temperature": temperature, "messages": messages}
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        url = chat_completions_url(self.base_url)
        headers = {"Content-Type": "application/json"}
        secret = resolve_secret(self.secret_reference)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        start_time = time.monotonic()
        start_dt = datetime.now(UTC)
        try:
            with _OPENER.open(req, timeout=timeout) as response:
                raw_bytes = response.read()
                if response.status >= 400:
                    raise InferenceUnavailableError(f"inference request failed with HTTP {response.status}")
                body = json.loads(raw_bytes.decode("utf-8"))
        except TimeoutError as exc:
            _log_interaction(
                event=event,
                base_url=self.base_url,
                model=self.model,
                request_payload=payload,
                response_payload=None,
                start=start_dt,
                error=str(exc),
                redact_content=redact_content,
            )
            raise InferenceTimeoutError(f"inference request timed out after {timeout}s") from exc
        except HTTPError as exc:
            _log_interaction(
                event=event,
                base_url=self.base_url,
                model=self.model,
                request_payload=payload,
                response_payload=None,
                start=start_dt,
                error=str(exc),
                redact_content=redact_content,
            )
            raise InferenceUnavailableError(f"inference request failed with HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            _log_interaction(
                event=event,
                base_url=self.base_url,
                model=self.model,
                request_payload=payload,
                response_payload=None,
                start=start_dt,
                error=str(exc),
                redact_content=redact_content,
            )
            if isinstance(exc.reason, socket.timeout):
                raise InferenceTimeoutError(f"inference request timed out after {timeout}s") from exc
            raise InferenceUnavailableError(f"inference request failed: {exc.reason}") from exc
        except OSError as exc:
            _log_interaction(
                event=event,
                base_url=self.base_url,
                model=self.model,
                request_payload=payload,
                response_payload=None,
                start=start_dt,
                error=str(exc),
                redact_content=redact_content,
            )
            raise InferenceUnavailableError(f"inference request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            _log_interaction(
                event=event,
                base_url=self.base_url,
                model=self.model,
                request_payload=payload,
                response_payload=None,
                start=start_dt,
                error=str(exc),
                redact_content=redact_content,
            )
            raise InferenceUnavailableError("inference returned invalid JSON") from exc

        _log_interaction(
            event=event,
            base_url=self.base_url,
            model=self.model,
            request_payload=payload,
            response_payload=body,
            start=start_dt,
            redact_content=redact_content,
        )

        if not isinstance(body, dict):
            raise InferenceUnavailableError("inference response body is not a JSON object")

        latency_ms = int((time.monotonic() - start_time) * 1000)
        content = _extract_content(body)
        usage = _parse_usage(body, payload, content)
        return InferenceResponse(content=content, usage=usage, raw_response=body, latency_ms=latency_ms)


def _extract_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InferenceUnavailableError("inference response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise InferenceUnavailableError("inference choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise InferenceUnavailableError("inference choice missing message object")
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return " ".join(parts)
    return str(content)


def chat_completions_request(
    *,
    base_url: str,
    model: str,
    secret_reference: str | None = None,
    messages: list[dict[str, Any]],
    temperature: float = 0.0,
    max_tokens: int = 0,
    timeout_seconds: int = 120,
    event: str = "chat",
    redact_content: bool = True,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute chat completions and return the raw JSON dict response."""
    client = InferenceClient(
        base_url=base_url, model=model, secret_reference=secret_reference, timeout_seconds=timeout_seconds
    )
    response = client.chat_completions(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        event=event,
        redact_content=redact_content,
        response_format=response_format,
    )
    return response.raw_response


def probe_openai_compatible_endpoint(
    *, base_url: str, model: str, secret_reference: str | None = None, timeout_seconds: int = 5
) -> None:
    client = InferenceClient(
        base_url=base_url, model=model, secret_reference=secret_reference, timeout_seconds=timeout_seconds
    )
    status, _, detail = client.probe(timeout_seconds=timeout_seconds)
    if status != "healthy":
        raise InferenceUnavailableError(f"inference health probe failed: {detail}")
