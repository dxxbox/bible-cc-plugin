"""BiBLE Atlas V4 HTTP client — single channel for all BiBLE API communication.

Used by both daemon and MCP server. Encapsulates auth, timeouts, error
classification, and tracing. All public methods return parsed responses;
HTTP-level failures are raised as BiBLEError (4xx) or BibleUnreachableError
(5xx / timeout / network error).

Design: Phase 3a backlog (2026-06-24-phase-3a-bible-client.md).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from bible_cc_plugin.config import BibleConfig
from bible_cc_plugin.logging_config import get_logger

_log = get_logger("client")

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
    latency_ms: float


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
            content_type, body_len,
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
            content_type, body_len,
        )
        return {}


def _strip_trailing_slash(url: str) -> str:
    """Remove trailing slash so path concatenation is safe."""
    return url.rstrip("/")


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
    ) -> httpx.Response:
        """Send an HTTP request to BiBLE Atlas.

        All public API methods route through here, guaranteeing consistent
        auth, timing, logging, and error translation.

        Returns the raw ``httpx.Response`` — callers parse the body.
        Raises ``BiBLEError`` on 4xx, ``BibleUnreachableError`` on 5xx /
        timeout / network error.
        """
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        url = f"{self._base_url}{path}"
        start = time.monotonic()

        try:
            response = await self._http.request(
                method=method,
                url=url,
                json=json_body,
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            latency = (time.monotonic() - start) * 1000
            _log.error(
                "[bible:req] %s %s → UNREACHABLE (%.0fms) timeout",
                method, path, latency,
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
            _log.error(
                "[bible:req] %s %s → UNREACHABLE (%.0fms) %s: %s",
                method, path, latency, type(exc).__name__, exc,
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
            _log.error(
                "[bible:req] %s %s → %d (%.0fms) %s",
                method, path, status, latency, code,
            )
            raise BiBLEError(code=code, message=message, status_code=status)

        if status >= 500:
            _log.error(
                "[bible:req] %s %s → %d (%.0fms) server error",
                method, path, status, latency,
            )
            raise BibleUnreachableError(
                f"BiBLE Atlas returned {status} ({method} {path})"
            )

        return response

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def check_health(self) -> HealthResult:
        """Check BiBLE Atlas connectivity.

        Returns a ``HealthResult`` — never raises (unreachable is
        expressed as ``reachable=False``, not an exception).
        """
        try:
            response = await self._request("GET", "/health")
            body = _parse_json(response)
            healthy = body.get("status") == "ok"
            _log.info(
                "[bible:req] GET /health → %d (%.0fms) reachable=%s",
                response.status_code,
                response.elapsed.total_seconds() * 1000,
                healthy,
            )
            if healthy:
                return HealthResult(
                    reachable=True,
                    latency_ms=response.elapsed.total_seconds() * 1000,
                )
            return HealthResult(reachable=False, latency_ms=0.0)
        except (BiBLEError, BibleUnreachableError):
            return HealthResult(reachable=False, latency_ms=0.0)

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
                "[bible:req] POST %s → %d (%.0fms) INVALID_RESPONSE: "
                "missing results.%s",
                path, response.status_code,
                response.elapsed.total_seconds() * 1000,
                domain_key,
            )
            raise BiBLEError(
                code="INVALID_RESPONSE",
                message=(
                    f"Search response missing results.{domain_key} "
                    f"(path={path})"
                ),
                status_code=response.status_code,
            )
        _log.info(
            "[bible:req] POST %s → 200 (%.1fs) total=%d",
            path, response.elapsed.total_seconds(), total,
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
            "/api/search/memory", "memory",
            query, tag,
            top_k=top_k, search_type=search_type,
            kb_index=kb_index, vector_model=vector_model,
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
            "/api/search/knowledge-base", "knowledge_base",
            query, tag,
            top_k=top_k, search_type=search_type,
            kb_index=kb_index, vector_model=vector_model,
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
            "/api/search/skill", "skill",
            query, tag,
            top_k=top_k, search_type=search_type,
            kb_index=kb_index, vector_model=vector_model,
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
            "POST", "/api/import/memory",
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
                "[bible:req] POST %s → %d (%.0fms) "
                "INVALID_RESPONSE: missing task_id",
                path, response.status_code,
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
            "/api/download/memory/file", storage_path, tag, download_name,
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
            "/api/download/skill/file", storage_path, tag, download_name,
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
            "GET", f"/api/control/admin/tasks/{task_id}",
        )
        data = _parse_json(response)
        status_value = data.get("status")
        if not status_value:
            _log.error(
                "[bible:req] GET /api/control/admin/tasks/%s → %d (%.0fms) "
                "INVALID_RESPONSE: missing status",
                task_id, response.status_code,
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
            "GET", f"/api/download/{domain}/artifact/{artifact_id}",
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
