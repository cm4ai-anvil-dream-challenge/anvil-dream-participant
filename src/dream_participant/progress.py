"""Terminal progress and durable participant-run logging."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def _timestamp(*, short: bool = False) -> str:
    format_string = "%H:%M:%S" if short else "%Y-%m-%d %H:%M:%S"
    return datetime.now().astimezone().strftime(format_string)


class ProgressReporter:
    """Write timestamped terminal progress and a durable run log."""

    def __init__(self, stream=None, log_path: Path | None = None) -> None:
        self.stream = stream or sys.stderr
        self.log_path = log_path or (Path.cwd() / "dream-participant.log")
        self._lock = threading.RLock()
        self._announced = False
        self._last_phase: str | None = None
        self._last_phase_at = time.monotonic()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_suppression_depth = 0

    def _write_log(self, message: str) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{_timestamp()}] {message}\n")
        except OSError:
            # Logging must never prevent a submission from running.
            pass

    def _write_terminal(self, text: str) -> None:
        self.stream.write(text + "\n")
        self.stream.flush()

    def phase(self, message: str) -> None:
        """Write a timestamped phase message immediately."""
        with self._lock:
            self._last_phase = message
            self._last_phase_at = time.monotonic()
            if not self._announced:
                self._write_terminal(
                    f"[{_timestamp(short=True)}] Log file: {self.log_path}"
                )
                self._write_log(f"Log file: {self.log_path}")
                self._announced = True
            self._write_terminal(f"[{_timestamp(short=True)}] {message}")
            self._write_log(message)

    def start_heartbeat(self, interval_seconds: float = 60.0) -> None:
        """Report the current phase while a remote call remains silent."""
        if self._heartbeat_thread is not None:
            return

        def run() -> None:
            while not self._heartbeat_stop.wait(interval_seconds):
                with self._lock:
                    if self._last_phase is None or self._heartbeat_suppression_depth:
                        continue
                    elapsed = time.monotonic() - self._last_phase_at
                    if elapsed < interval_seconds:
                        continue
                    message = f"Still working: {self._last_phase} ({elapsed:.0f}s)"
                    self._write_terminal(f"[{_timestamp(short=True)}] {message}")
                    self._write_log(message)

        self._heartbeat_thread = threading.Thread(target=run, daemon=True)
        self._heartbeat_thread.start()

    @contextmanager
    def suppress_heartbeat(self) -> Iterator[None]:
        """Suppress heartbeat messages during a planned wait."""
        with self._lock:
            self._heartbeat_suppression_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._heartbeat_suppression_depth -= 1

    def close(self, *, failed: bool = False) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1)
        with self._lock:
            if failed:
                self._write_log("Run failed")


_REPORTER = ProgressReporter()


def progress(message: str) -> None:
    _REPORTER.phase(message)


def close_progress(*, failed: bool = False) -> None:
    _REPORTER.close(failed=failed)
