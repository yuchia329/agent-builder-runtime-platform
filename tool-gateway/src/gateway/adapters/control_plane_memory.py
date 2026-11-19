"""ControlPlane in process memory: tests. Runs are put with `put`; `fail` makes reads fail."""

import copy
import threading
from typing import Any

from gateway.ports.control_plane import ControlPlaneUnavailable, RunNotFound


class MemoryControlPlane:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.unavailable = False
        self.reads: list[str] = []

    def put(self, run: dict[str, Any]) -> None:
        with self._lock:
            self._runs[run["run_id"]] = copy.deepcopy(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            self.reads.append(run_id)
            if self.unavailable:
                raise ControlPlaneUnavailable("control plane unavailable")
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            return copy.deepcopy(run)
