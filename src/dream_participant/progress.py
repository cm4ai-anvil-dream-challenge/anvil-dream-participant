"""Terminal progress and durable participant-run logging."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from tqdm import tqdm


@contextmanager
def transfer_progress(description: str) -> Iterator[Callable[[int, int], None]]:
    """Track cumulative bytes, including a restart from zero after a retry."""
    with tqdm(total=None, desc=description, unit="B", unit_scale=True,
              unit_divisor=1024, mininterval=0.2) as bar:
        def update(completed: int, total: int) -> None:
            bar.total = total
            if completed < bar.n:
                bar.reset(total=total)
            bar.update(completed - bar.n)
        yield update


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
            if not self._announced:
                self._write_terminal(
                    f"[{_timestamp(short=True)}] Log file: {self.log_path}"
                )
                self._write_log(f"Log file: {self.log_path}")
                self._announced = True
            self._write_terminal(f"[{_timestamp(short=True)}] {message}")
            self._write_log(message)

    def close(self, *, failed: bool = False) -> None:
        with self._lock:
            if failed:
                self._write_log("Run failed")


_REPORTER = ProgressReporter()


def progress(message: str) -> None:
    _REPORTER.phase(message)


def close_progress(*, failed: bool = False) -> None:
    _REPORTER.close(failed=failed)
