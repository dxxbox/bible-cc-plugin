"""BiBLE Atlas V4 HTTP client — single channel for all BiBLE API communication.

Used by both daemon and MCP server. Encapsulates auth, timeouts, error
classification, and tracing. All public methods return parsed responses;
HTTP-level failures are raised as BiBLEError (4xx) or BibleUnreachableError
(5xx / timeout / network error).

Design: Phase 3a backlog (2026-06-24-phase-3a-bible-client.md).
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from bible_cc_plugin.config import BibleConfig
from bible_cc_plugin.logging_config import get_logger

_log = get_logger("client")

# Phase 3c: health probes are latency-sensitive — 3s cap (not the client's
# default connect/read timeouts).
HEALTH_PROBE_TIMEOUT_S: float = 3.0
# Ring buffer size for the /daemon/debug/bible-requests diagnostic endpoint.
REQUEST_LOG_MAXLEN: int = 200

# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class BiBLEError(Exception):
    """4xx response — configuration or request error. Not retryable."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")


class BibleUnreachableError(Exception):
    """5xx / timeout / network error — temporary failure, retryable."""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        self.original_error = original_error
        super().__init__(message)


@dataclass
class HealthResult:
    """Outcome of a BiBLE health check."""

    reachable: bool
    latency_ms: float | None
    base_url: str = ""
    error: str | None = None
    steps: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_json(response: httpx.Response) -> dict[str, Any]:
    """Parse a 2xx JSON response body — raises BiBLEError on failure.

    Used for all success-path parsing.  Callers that need tolerant parsing
    (e.g. error-body extraction) should use ``_safe_json()`` instead.
    """
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        content_type = response.headers.get("content-type", "unknown")
        body_len = len(response.content) if response.content else 0
        _log.error(
            "[bible:req] non-JSON 2xx response: content-type=%s, body-length=%d",
            content_type,
            body_len,
        )
        raise BiBLEError(
            code="INVALID_RESPONSE",
            message=(
                f"BiBLE returned non-JSON response "
                f"(content-type={content_type}, body-length={body_len})"
            ),
            status_code=response.status_code,
        )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    """Parse JSON body defensively — returns {} on failure.

    Use ONLY for error-response body extraction where the body is
    informational and a parse failure should not mask the original error.
    For success-path parsing use ``_parse_json()`` instead.
    """
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        content_type = response.headers.get("content-type", "unknown")
        body_len = len(response.content) if response.content else 0
        _log.warning(
            "[bible:req] non-JSON response body: content-type=%s, body-length=%d",
            content_type,
            body_len,
        )
        return {}


def _strip_trailing_slash(url: str) -> str:
    """Remove trailing slash so path concatenation is safe."""
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Request ring buffer (Phase 3c — /daemon/debug/bible-requests)
# ---------------------------------------------------------------------------

_request_log: deque[dict[str, Any]] = deque(maxlen=REQUEST_LOG_MAXLEN)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sanitize_path(path: str) -> str:
    """Strip query/fragment and cap length — never record secrets."""
    clean = urlsplit(path).path or path
    return clean[:200]


def _record_request(
    method: str,
    path: str,
    *,
    status: int | None,
    latency_ms: float,
    http_ok: bool,
    error: str | None = None,
) -> None:
    """Append one HTTP-layer entry to the in-memory request ring buffer."""
    clean = _sanitize_path(path)
    _request_log.append(
        {
            "time": _utc_now_iso(),
            "method": method,
            "path": clean,
            "status": status,
            "latency_ms": round(latency_ms, 1),
            "http_ok": http_ok,
            "is_health": clean == "/health",
            "error": error,
        }
    )


def get_recent_requests(limit: int = 50, *, include_health: bool = False) -> list[dict[str, Any]]:
    """Return recent request-log entries, newest first.

    Health probes (every 30s) would otherwise dominate the 200-entry window;
    they are hidden unless ``include_health=True``.
    """
    entries = [e for e in _request_log if include_health or not e["is_health"]]
    return list(reversed(entries[-limit:]))


def clear_request_log() -> None:
    """Clear the request ring buffer (test helper)."""
    _request_log.clear()


# ---------------------------------------------------------------------------
# Connectivity probe helpers (Phase 3c — verbose health)
# ---------------------------------------------------------------------------


def _short_error(exc: Exception) -> str:
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg[:120]}" if msg else type(exc).__name__


def _probe_step_error(exc: Exception) -> str:
    """Map a probe-stage exception to a short diagnostic label."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, BiBLEError):
        return f"http {exc.status_code}: {exc.code}"
    if isinstance(exc, BibleUnreachableError):
        original = exc.original_error
        if isinstance(original, httpx.TimeoutException):
            return "timeout"
        if isinstance(original, httpx.ConnectError):
            msg = str(original).lower()
            if "refused" in msg:
                return "connection refused"
            return f"connect error: {str(original)[:120]}"
        return _short_error(original) if original is not None else "unreachable"
    if isinstance(exc, socket.gaierror):
        return "NXDOMAIN"
    if isinstance(exc, OSError) and "refused" in str(exc).lower():
        return "connection refused"
    return _short_error(exc)


def _describe_health_error(exc: Exception) -> str:
    """Human-readable failure reason for the non-verbose health path."""
    if isinstance(exc, BiBLEError):
        return f"http {exc.status_code}: {exc.code}"
    if isinstance(exc, BibleUnreachableError):
        original = exc.original_error
        if original is not None:
            return _probe_step_error(exc)
        return str(exc)[:160]
    return _short_error(exc)


def _parse_target(base_url: str) -> tuple[str, int]:
    """Extract (host, port) from a base URL."""
    parsed = urlsplit(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _dns_lookup(host: str, port: int) -> list[str]:
    """Resolve host to distinct addresses (IPv4/IPv6), preserving order."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addrs: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in addrs:
            addrs.append(addr)
    return addrs


def _tcp_probe(addresses: list[str], port: int, timeout: float) -> str:
    """Try each resolved address until one connects; return the connected IP."""
    last_exc: Exception | None = None
    for addr in addresses:
        try:
            with socket.create_connection((addr, port), timeout=timeout):
                return addr
        except OSError as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise OSError("no addresses to probe")


# ---------------------------------------------------------------------------
# BiBLEClient
# ---------------------------------------------------------------------------


class BiBLEClient:
    """Async HTTP client for BiBLE Atlas V4 REST API.

    Usage::

        config = load_config()
        client = BiBLEClient(config.bible)
        health = await client.check_health()
        # …
        await client.aclose()

    Or as a context manager::

        async with BiBLEClient(config.bible) as client:
            results = await client.search_memory("redis pattern")

    Lifecycle is managed externally:
    - Daemon: FastAPI lifespan (app.state.client)
    - MCP server: created at startup, closed in ``finally``
    - Tests: ``async with`` or ``pytest-httpx`` mock
    """

    __slots__ = ("_base_url", "_token", "_kb_index", "_client", "_timeout")

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: BibleConfig) -> None:
        self._base_url = _strip_trailing_slash(config.base_url)
        self._token = config.token
        self._kb_index = config.kb_index
        self._timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        """Lazy-init the inner AsyncClient on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BiBLEClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Core request method (single HTTP entry point)
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, str | bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Send an HTTP request to BiBLE Atlas.

        All public API methods route through here, guaranteeing consistent
        auth, timing, logging, and error translation.

        ``timeout`` overrides the client-wide timeout for this request only.
        It is passed to httpx **only when not None** — httpx treats
        ``timeout=None`` as "no timeout", which would silently disable the
        default connect/read limits.

        Returns the raw ``httpx.Response`` — callers parse the body.
        Raises ``BiBLEError`` on 4xx, ``BibleUnreachableError`` on 5xx /
        timeout / network error.
        """
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        url = f"{self._base_url}{path}"
        start = time.monotonic()

        request_kwargs: dict[str, Any] = {
            "method": method,
            "url": url,
            "json": json_body,
            "data": data,
            "files": files,
            "headers": headers,
        }
        if timeout is not None:
            request_kwargs["timeout"] = timeout

        try:
            response = await self._http.request(**request_kwargs)
        except httpx.TimeoutException as exc:
            latency = (time.monotonic() - start) * 1000
            _record_request(
                method,
                path,
                status=None,
                latency_ms=latency,
                http_ok=False,
                error="timeout",
            )
            _log.error(
                "[bible:req] %s %s → UNREACHABLE (%.0fms) timeout",
                method,
                path,
                latency,
            )
            raise BibleUnreachableError(
                f"Timeout connecting to BiBLE Atlas ({method} {path})",
                original_error=exc,
            ) from exc
        except httpx.RequestError as exc:
            # Catch-all for transport/protocol/network errors:
            # ConnectError, NetworkError, TransportError,
            # RemoteProtocolError, LocalProtocolError, CloseError,
            # ReadError, WriteError, PoolTimeout — all mapped to
            # BibleUnreachableError so callers have a single degradation path.
            latency = (time.monotonic() - start) * 1000
            _record_request(
                method,
                path,
                status=None,
                latency_ms=latency,
                http_ok=False,
                error=_short_error(exc)[:200],
            )
            _log.error(
                "[bible:req] %s %s → UNREACHABLE (%.0fms) %s: %s",
                method,
                path,
                latency,
                type(exc).__name__,
                exc,
            )
            raise BibleUnreachableError(
                f"BiBLE Atlas unreachable: {type(exc).__name__} ({method} {path})",
                original_error=exc,
            ) from exc

        latency = (time.monotonic() - start) * 1000
        status = response.status_code

        if 400 <= status < 500:
            body = _safe_json(response)
            code = body.get("error", {}).get("code", "UNKNOWN")
            message = body.get("error", {}).get("message", response.text[:200])
            _record_request(
                method,
                path,
                status=status,
                latency_ms=latency,
                http_ok=False,
                error=str(code),
            )
            _log.error(
                "[bible:req] %s %s → %d (%.0fms) %s",
                method,
                path,
                status,
                latency,
                code,
            )
            raise BiBLEError(code=code, message=message, status_code=status)

        if status >= 500:
            _record_request(
                method,
                path,
                status=status,
                latency_ms=latency,
                http_ok=False,
                error=f"http {status}",
            )
            _log.error(
                "[bible:req] %s %s → %d (%.0fms) server error",
                method,
                path,
                status,
                latency,
            )
            raise BibleUnreachableError(f"BiBLE Atlas returned {status} ({method} {path})")

        _record_request(
            method,
            path,
            status=status,
            latency_ms=latency,
            http_ok=True,
        )
        return response

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def check_health(
        self,
        *,
        verbose: bool = False,
        timeout: float | None = None,
    ) -> HealthResult:
        """Check BiBLE Atlas connectivity.

        ``timeout`` defaults to :data:`HEALTH_PROBE_TIMEOUT_S` (3s) because
        health probes must stay latency-bounded; pass an explicit value to
        override.  There is no way to disable the timeout here.

        Returns a ``HealthResult`` — never raises (unreachable is
        expressed as ``reachable=False``, not an exception).  ``latency_ms``
        is ``None`` on failure; ``error`` carries a short reason.
        """
        effective_timeout = HEALTH_PROBE_TIMEOUT_S if timeout is None else timeout
        if verbose:
            return await self._check_health_verbose(effective_timeout)

        start = time.monotonic()
        try:
            response = await self._request("GET", "/health", timeout=effective_timeout)
            body = _parse_json(response)
            latency = (time.monotonic() - start) * 1000
            status_value = body.get("status")
            if status_value == "ok":
                _log.info(
                    "[bible:req] GET /health → %d (%.0fms) reachable=True",
                    response.status_code,
                    latency,
                )
                return HealthResult(
                    reachable=True,
                    latency_ms=round(latency, 1),
                    base_url=self._base_url,
                )
            _log.warning(
                "[bible:req] GET /health → %d (%.0fms) unreachable unhealthy status=%s",
                response.status_code,
                latency,
                status_value,
            )
            return HealthResult(
                reachable=False,
                latency_ms=None,
                base_url=self._base_url,
                error=f"unhealthy status={status_value}",
            )
        except (BiBLEError, BibleUnreachableError) as exc:
            error = _describe_health_error(exc)
            _log.error("[bible:req] GET /health → UNREACHABLE %s", error)
            return HealthResult(
                reachable=False,
                latency_ms=None,
                base_url=self._base_url,
                error=error,
            )

    async def _check_health_verbose(self, timeout: float) -> HealthResult:
        """DNS → TCP → HTTP probe with per-step results, total budget ``timeout``.

        Blocking socket calls run in a worker thread; every stage shares one
        deadline so the whole probe can never exceed ``timeout``.
        """
        host, port = _parse_target(self._base_url)
        probe_start = time.monotonic()
        deadline = probe_start + timeout
        steps: list[dict[str, Any]] = []

        def remaining() -> float:
            return max(0.001, deadline - time.monotonic())

        def result(reachable: bool, *, error: str | None = None) -> HealthResult:
            total = (time.monotonic() - probe_start) * 1000
            return HealthResult(
                reachable=reachable,
                latency_ms=round(total, 1) if reachable else None,
                base_url=self._base_url,
                error=error,
                steps=steps,
            )

        # 1) DNS -------------------------------------------------------
        dns_start = time.monotonic()
        try:
            addresses = await asyncio.wait_for(
                asyncio.to_thread(_dns_lookup, host, port), timeout=remaining()
            )
            dns_latency = (time.monotonic() - dns_start) * 1000
            steps.append(
                {
                    "step": "dns",
                    "ok": True,
                    "latency_ms": round(dns_latency, 1),
                    "detail": ", ".join(addresses[:4]),
                }
            )
        except Exception as exc:  # noqa: BLE001 — probe must never raise
            dns_latency = (time.monotonic() - dns_start) * 1000
            detail = "NXDOMAIN" if isinstance(exc, socket.gaierror) else _probe_step_error(exc)
            steps.append(
                {
                    "step": "dns",
                    "ok": False,
                    "latency_ms": round(dns_latency, 1),
                    "detail": detail,
                }
            )
            _log.error("[bible:req] health verbose dns failed: %s", detail)
            return result(False, error=f"dns: {detail}")

        # 2) TCP -------------------------------------------------------
        tcp_start = time.monotonic()
        try:
            connected = await asyncio.wait_for(
                asyncio.to_thread(_tcp_probe, addresses, port, remaining()),
                timeout=remaining(),
            )
            tcp_latency = (time.monotonic() - tcp_start) * 1000
            steps.append(
                {
                    "step": "tcp",
                    "ok": True,
                    "latency_ms": round(tcp_latency, 1),
                    "detail": f"{connected}:{port}",
                }
            )
        except Exception as exc:  # noqa: BLE001 — probe must never raise
            tcp_latency = (time.monotonic() - tcp_start) * 1000
            detail = _probe_step_error(exc)
            steps.append(
                {
                    "step": "tcp",
                    "ok": False,
                    "latency_ms": round(tcp_latency, 1),
                    "detail": detail,
                }
            )
            _log.error("[bible:req] health verbose tcp failed: %s", detail)
            return result(False, error=f"tcp: {detail}")

        # 3) HTTP ------------------------------------------------------
        http_start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._request("GET", "/health", timeout=remaining()),
                timeout=remaining(),
            )
            body = _parse_json(response)
            http_latency = (time.monotonic() - http_start) * 1000
            healthy = body.get("status") == "ok"
            steps.append(
                {
                    "step": "http",
                    "ok": healthy,
                    "latency_ms": round(http_latency, 1),
                    "detail": (
                        f"status={response.status_code}"
                        if healthy
                        else f"unhealthy status={body.get('status')}"
                    ),
                }
            )
            if healthy:
                return result(True)
            return result(False, error=f"unhealthy status={body.get('status')}")
        except Exception as exc:  # noqa: BLE001 — probe must never raise
            http_latency = (time.monotonic() - http_start) * 1000
            detail = _probe_step_error(exc)
            steps.append(
                {
                    "step": "http",
                    "ok": False,
                    "latency_ms": round(http_latency, 1),
                    "detail": detail,
                }
            )
            _log.error("[bible:req] health verbose http failed: %s", detail)
            return result(False, error=f"http: {detail}")

    # ------------------------------------------------------------------
    # Search methods (three domains)
    # ------------------------------------------------------------------

    async def _search(
        self,
        path: str,
        domain_key: str,
        query: str,
        tag: str,
        *,
        top_k: int | None = None,
        search_type: str | None = None,
        kb_index: str | None = None,
        vector_model: str | None = None,
        vector_weight: float | None = None,
    ) -> list[dict[str, Any]]:
        """Shared search logic for the three domain endpoints.

        Extracts results from ``results.<domain_key>`` (snake_case key).
        """
        body: dict[str, Any] = {"query": query, "tag": tag}
        if top_k is not None:
            body["top_k"] = top_k
        if search_type is not None:
            body["search_type"] = search_type
        if kb_index is not None:
            body["kb_index"] = kb_index
        if vector_model is not None:
            body["vector_model"] = vector_model
        if vector_weight is not None:
            body["vector_weight"] = vector_weight

        response = await self._request("POST", path, json_body=body)
        data = _parse_json(response)
        total = data.get("total", 0)
        results = data.get("results")
        if not isinstance(results, dict) or domain_key not in results:
            _log.error(
                "[bible:req] POST %s → %d (%.0fms) INVALID_RESPONSE: missing results.%s",
                path,
                response.status_code,
                response.elapsed.total_seconds() * 1000,
                domain_key,
            )
            raise BiBLEError(
                code="INVALID_RESPONSE",
                message=(f"Search response missing results.{domain_key} (path={path})"),
                status_code=response.status_code,
            )
        _log.info(
            "[bible:req] POST %s → 200 (%.1fs) total=%d",
            path,
            response.elapsed.total_seconds(),
            total,
        )
        return results[domain_key]

    async def search_memory(
        self,
        query: str,
        tag: str = "memory",
        *,
        top_k: int | None = None,
        search_type: str | None = None,
        kb_index: str | None = None,
        vector_model: str | None = None,
        vector_weight: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search the MEMORY domain."""
        return await self._search(
            "/api/search/memory",
            "memory",
            query,
            tag,
            top_k=top_k,
            search_type=search_type,
            kb_index=kb_index,
            vector_model=vector_model,
            vector_weight=vector_weight,
        )

    async def search_knowledge_base(
        self,
        query: str,
        tag: str,
        *,
        top_k: int | None = None,
        search_type: str | None = None,
        kb_index: str | None = None,
        vector_model: str | None = None,
        vector_weight: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search the KNOWLEDGE_BASE domain.  *tag* is required (no default)."""
        return await self._search(
            "/api/search/knowledge-base",
            "knowledge_base",
            query,
            tag,
            top_k=top_k,
            search_type=search_type,
            kb_index=kb_index,
            vector_model=vector_model,
            vector_weight=vector_weight,
        )

    async def search_skill(
        self,
        query: str,
        tag: str = "skill",
        *,
        top_k: int | None = None,
        search_type: str | None = None,
        kb_index: str | None = None,
        vector_model: str | None = None,
        vector_weight: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search the SKILL domain."""
        return await self._search(
            "/api/search/skill",
            "skill",
            query,
            tag,
            top_k=top_k,
            search_type=search_type,
            kb_index=kb_index,
            vector_model=vector_model,
            vector_weight=vector_weight,
        )

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    async def import_memory(
        self,
        files: list[tuple[str, bytes, str]],
        kb_index: str | None = None,
        tag: str = "memory",
        *,
        parser_script: tuple[str, bytes, str] | None = None,
        vector_model: str | None = None,
        parser_context: str | None = None,
    ) -> dict[str, Any]:
        """Import moments into BiBLE Atlas via multipart upload.

        *files* is a list of ``(filename, content_bytes, content_type)``
        tuples.  Serialization is the caller's responsibility (daemon flush
        layer or MCP adapter).

        Optional import fields:

        - ``parser_script`` — ``(filename, content, content_type)`` tuple,
          sent as its own ``parser_script`` multipart field (V4 type: **file**).
        - ``vector_model`` — string, sent as a form field (V4 type: string).
        - ``parser_context`` — JSON string, sent as a form field.

        Returns the API response dict, e.g. ``{"task_id": "abc-123", ...}``.

        Raises ``BiBLEError`` when *files* is empty — BiBLE V4 requires at
        least one ``files[]`` field.
        """
        if not files:
            raise BiBLEError(
                code="INVALID_REQUEST",
                message="import_memory requires at least one file",
                status_code=400,
            )

        kb = kb_index if kb_index is not None else self._kb_index
        form_data: dict[str, str] = {"kb_index": kb, "tag": tag}
        if vector_model is not None:
            form_data["vector_model"] = vector_model
        if parser_context is not None:
            form_data["parser_context"] = parser_context
        # httpx expects files as list[tuple[str, tuple[str, bytes, str]]]
        httpx_files: list[tuple[str, tuple[str, bytes, str]]] = []
        for filename, content, content_type in files:
            httpx_files.append(("files[]", (filename, content, content_type)))
        if parser_script is not None:
            # parser_script is its own multipart field, not a files[] entry
            httpx_files.append(("parser_script", parser_script))

        response = await self._request(
            "POST",
            "/api/import/memory",
            data=form_data,
            files=httpx_files,
        )
        data = _parse_json(response)
        task_id = data.get("task_id")
        if not task_id:
            _log.error(
                "[bible:req] POST /api/import/memory → %d (%.0fms) "
                "INVALID_RESPONSE: missing task_id",
                response.status_code,
                response.elapsed.total_seconds() * 1000,
            )
            raise BiBLEError(
                code="INVALID_RESPONSE",
                message="Import response missing task_id",
                status_code=response.status_code,
            )
        _log.info(
            "[bible:req] POST /api/import/memory → %d (%.1fs) task_id=%s",
            response.status_code,
            response.elapsed.total_seconds(),
            task_id,
        )
        return data

    # ------------------------------------------------------------------
    # Download (async three-step flow)
    # ------------------------------------------------------------------

    async def _submit_download(
        self,
        path: str,
        storage_path: str,
        tag: str,
        download_name: str | None = None,
    ) -> dict[str, Any]:
        """Submit a download task (step 1/3)."""
        body: dict[str, str] = {"tag": tag, "storage_path": storage_path}
        if download_name is not None:
            body["download_name"] = download_name

        response = await self._request("POST", path, json_body=body)
        data = _parse_json(response)
        task_id = data.get("task_id")
        if not task_id:
            _log.error(
                "[bible:req] POST %s → %d (%.0fms) INVALID_RESPONSE: missing task_id",
                path,
                response.status_code,
                response.elapsed.total_seconds() * 1000,
            )
            raise BiBLEError(
                code="INVALID_RESPONSE",
                message=f"Download response missing task_id (path={path})",
                status_code=response.status_code,
            )
        _log.info(
            "[bible:req] POST %s → %d (%.1fs) task_id=%s",
            path,
            response.status_code,
            response.elapsed.total_seconds(),
            task_id,
        )
        return data

    async def request_memory_download(
        self,
        storage_path: str,
        tag: str = "memory",
        download_name: str | None = None,
    ) -> dict[str, Any]:
        """Submit a memory download task (async step 1/3).

        Callers must poll ``get_task_status()`` then call
        ``get_download_artifact()`` to retrieve the file.
        """
        return await self._submit_download(
            "/api/download/memory/file",
            storage_path,
            tag,
            download_name,
        )

    async def request_skill_download(
        self,
        storage_path: str,
        tag: str = "skill",
        download_name: str | None = None,
    ) -> dict[str, Any]:
        """Submit a skill download task (async step 1/3).

        Callers must poll ``get_task_status()`` then call
        ``get_download_artifact()`` to retrieve the file.
        """
        return await self._submit_download(
            "/api/download/skill/file",
            storage_path,
            tag,
            download_name,
        )

    # ------------------------------------------------------------------
    # Task status
    # ------------------------------------------------------------------

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Poll an async import/download task status (step 2/3).

        Returns the full task record including ``status`` (queued / running /
        completed / failed / cancelled) and, when completed, ``artifact_id``.
        """
        response = await self._request(
            "GET",
            f"/api/control/admin/tasks/{task_id}",
        )
        data = _parse_json(response)
        status_value = data.get("status")
        if not status_value:
            _log.error(
                "[bible:req] GET /api/control/admin/tasks/%s → %d (%.0fms) "
                "INVALID_RESPONSE: missing status",
                task_id,
                response.status_code,
                response.elapsed.total_seconds() * 1000,
            )
            raise BiBLEError(
                code="INVALID_RESPONSE",
                message=f"Task status response missing 'status' field (task_id={task_id})",
                status_code=response.status_code,
            )
        _log.info(
            "[bible:req] GET /api/control/admin/tasks/%s → %d (%.1fs) status=%s",
            task_id,
            response.status_code,
            response.elapsed.total_seconds(),
            status_value,
        )
        return data

    # ------------------------------------------------------------------
    # Artifact download
    # ------------------------------------------------------------------

    async def get_download_artifact(
        self,
        domain: str,
        artifact_id: str,
    ) -> bytes:
        """Retrieve a completed download artifact as raw bytes (step 3/3).

        *domain* must be ``"memory"`` or ``"skill"``.
        """
        response = await self._request(
            "GET",
            f"/api/download/{domain}/artifact/{artifact_id}",
        )
        content = response.content
        _log.info(
            "[bible:req] GET /api/download/%s/artifact/%s → %d (%.1fs) size=%d",
            domain,
            artifact_id,
            response.status_code,
            response.elapsed.total_seconds(),
            len(content),
        )
        return content
