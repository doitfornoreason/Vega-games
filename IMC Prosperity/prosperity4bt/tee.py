"""Lightweight Tee replacement for IPython.utils.io.Tee.

Captures stdout to a StringIO while optionally also printing to the real stdout.
Removes the heavy IPython dependency.
"""

import sys
from io import StringIO
from typing import Optional


class Tee:
    """Duplicates writes to both a buffer and the original stdout."""

    def __init__(self, buffer: StringIO, channel: str = "stdout") -> None:
        self._buffer = buffer
        self._channel = channel
        self._original: Optional[object] = None

    def __enter__(self) -> "Tee":
        self._original = getattr(sys, self._channel)
        setattr(sys, self._channel, self)
        return self

    def __exit__(self, *args: object) -> None:
        setattr(sys, self._channel, self._original)

    def write(self, data: str) -> int:
        self._buffer.write(data)
        if self._original is not None:
            self._original.write(data)  # type: ignore[union-attr]
        return len(data)

    def flush(self) -> None:
        self._buffer.flush()
        if self._original is not None:
            self._original.flush()  # type: ignore[union-attr]
