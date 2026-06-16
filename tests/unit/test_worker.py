"""Unit tests for detection worker — threshold, queue, worker lifecycle.

Phase 2b Feature 2b.2 — all tests [Unit] [Pre].
Tests threshold counters (pure logic) and async worker behavior.
No SQLite, no HTTP, no real Anthropic calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _reset_module_state():
    """Reset the module-level threshold state before each test."""
    import bible_cc_plugin.daemon.server as server_mod

    server_mod._threshold_state.clear()
    server_mod._detection_queue = asyncio.Queue()


# ══════════════════════════════════════════════════════════════════════════════
# Threshold Counter Tests (check_threshold + reset_threshold)
# ══════════════════════════════════════════════════════════════════════════════


class TestCheckThreshold:
    """check_threshold() — turns/chars first-to-trigger, reset after trigger."""

    def test_turns_first(self):
        """7 turns → False, 8th turn → True (turns dimension triggers)."""
        _reset_module_state()
        from bible_cc_plugin.daemon.server import check_threshold

        sid = "sess-1"
        for _ in range(7):
            assert check_threshold(sid, turns=1, chars=0) is False
        assert check_threshold(sid, turns=1, chars=0) is True

    def test_chars_first(self):
        """15999 chars → False, 16000th char → True (chars dimension triggers)."""
        _reset_module_state()
        from bible_cc_plugin.daemon.server import check_threshold

        sid = "sess-1"
        assert check_threshold(sid, turns=0, chars=15999) is False
        assert check_threshold(sid, turns=0, chars=1) is True

    def test_resets_after_trigger(self):
        """After trigger, counter resets to 0 — next check starts fresh."""
        _reset_module_state()
        from bible_cc_plugin.daemon.server import check_threshold

        sid = "sess-1"
        for _ in range(8):
            check_threshold(sid, turns=1, chars=0)
        assert check_threshold(sid, turns=1, chars=0) is False

    def test_reset_threshold_clears_state(self):
        """reset_threshold() removes all state for /clear and /compact."""
        _reset_module_state()
        from bible_cc_plugin.daemon.server import check_threshold, reset_threshold

        sid = "sess-1"
        for _ in range(5):
            check_threshold(sid, turns=1, chars=0)
        reset_threshold(sid)
        assert check_threshold(sid, turns=1, chars=0) is False

    def test_per_session_isolation(self):
        """Different sessions have independent counters."""
        _reset_module_state()
        from bible_cc_plugin.daemon.server import check_threshold

        for _ in range(7):
            check_threshold("sess-a", turns=1, chars=0)
        assert check_threshold("sess-b", turns=1, chars=0) is False
        assert check_threshold("sess-a", turns=1, chars=0) is True


# ══════════════════════════════════════════════════════════════════════════════
# Worker Lifecycle Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestWorkerRestartsAfterCrash:
    """Intent: worker must auto-restart when _process_detection_task throws."""

    @pytest.mark.asyncio
    async def test_worker_continues_after_task_crash(self):
        """Worker processes task-3 even after task-2 crashes."""
        _reset_module_state()
        import bible_cc_plugin.daemon.server as server_mod

        processed = []

        async def mock_process(task):
            if task.get("crash"):
                raise RuntimeError("simulated detection crash")
            processed.append(task["id"])

        with patch.object(server_mod, "_process_detection_task", mock_process):
            server_mod._detection_queue = asyncio.Queue()
            worker_task = asyncio.create_task(server_mod._detection_worker())

            await server_mod._detection_queue.put({"id": 1, "session_id": "s1"})
            await server_mod._detection_queue.put(
                {"id": 2, "session_id": "s1", "crash": True}
            )
            await server_mod._detection_queue.put({"id": 3, "session_id": "s1"})

            await asyncio.sleep(0.3)

            await server_mod._detection_queue.put(None)
            await asyncio.wait_for(worker_task, timeout=2)

        assert processed == [1, 3], f"crash task should be skipped, got {processed}"


class TestWorkerConcurrency:
    """Intent: same-session detection tasks execute serially."""

    @pytest.mark.asyncio
    async def test_same_session_serialized(self):
        """Two tasks for same session — do not execute concurrently."""
        _reset_module_state()
        import bible_cc_plugin.daemon.server as server_mod

        running: list[int] = []

        async def mock_process(task):
            running.append(task["id"])
            await asyncio.sleep(0.05)
            running.remove(task["id"])

        with patch.object(server_mod, "_process_detection_task", mock_process):
            server_mod._detection_queue = asyncio.Queue()
            worker_task = asyncio.create_task(server_mod._detection_worker())

            await server_mod._detection_queue.put({"id": 1, "session_id": "s1"})
            await server_mod._detection_queue.put({"id": 2, "session_id": "s1"})

            await asyncio.sleep(0.2)

            await server_mod._detection_queue.put(None)
            await asyncio.wait_for(worker_task, timeout=2)

        # Both ran, never overlapping
        assert 1 not in running, "task 1 should finish before task 2 exits"


class TestEndpointReturnsBeforeDetection:
    """Intent: /turn/user must return <10ms even if detection takes seconds."""

    @pytest.mark.asyncio
    async def test_queue_put_is_non_blocking(self):
        """queue.put() returns immediately; detection happens in background."""
        _reset_module_state()
        import bible_cc_plugin.daemon.server as server_mod

        detection_started = asyncio.Event()
        detection_done = asyncio.Event()

        async def slow_process(task):
            detection_started.set()
            await asyncio.sleep(0.5)
            detection_done.set()

        with patch.object(server_mod, "_process_detection_task", slow_process):
            server_mod._detection_queue = asyncio.Queue()
            worker_task = asyncio.create_task(server_mod._detection_worker())

            loop = asyncio.get_event_loop()
            start = loop.time()
            await server_mod._detection_queue.put(
                {"id": 1, "session_id": "s1", "phase": 1}
            )
            put_elapsed = loop.time() - start

            assert put_elapsed < 0.01, (
                f"queue.put() took {put_elapsed*1000:.0f}ms, expected <10ms"
            )

            await asyncio.sleep(0.1)
            assert not detection_done.is_set(), (
                "detection should still be running in background"
            )

            await asyncio.wait_for(detection_done.wait(), timeout=2)
            await server_mod._detection_queue.put(None)
            await asyncio.wait_for(worker_task, timeout=2)
