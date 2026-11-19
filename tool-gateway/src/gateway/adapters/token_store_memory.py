"""TokenStore in process memory: tests and local runs. Lost on restart; not encrypted."""

import copy
import threading
from typing import Any

from gateway.ports.token_store import CredentialKey


class MemoryTokenStore:
    def __init__(self) -> None:
        self._values: dict[CredentialKey, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: CredentialKey) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.get(key)
            return None if value is None else copy.deepcopy(value)

    def put(self, key: CredentialKey, value: dict[str, Any]) -> None:
        with self._lock:
            self._values[key] = copy.deepcopy(value)

    def delete(self, key: CredentialKey) -> bool:
        with self._lock:
            return self._values.pop(key, None) is not None
