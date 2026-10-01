"""Saving and loading our own data files with msgspec.

Writes are atomic: the data goes to a temporary file that then replaces the old
one, so a crash mid-write never leaves a half-written file behind.
"""

import os
import tempfile
from pathlib import Path
from typing import Any

import msgspec


def load_msgpack[T](path: Path, type_: type[T]) -> T | None:
    """The saved value, or None if the file is missing, unreadable or out of date."""
    try:
        return msgspec.msgpack.decode(path.read_bytes(), type=type_)
    except OSError, msgspec.DecodeError:
        return None


def save_msgpack(path: Path, value: Any) -> None:
    _write_atomic(path, msgspec.msgpack.encode(value))


def load_json[T](path: Path, type_: type[T]) -> T | None:
    try:
        return msgspec.json.decode(path.read_bytes(), type=type_)
    except OSError, msgspec.DecodeError:
        return None


def save_json(path: Path, value: Any) -> None:
    _write_atomic(path, msgspec.json.format(msgspec.json.encode(value)) + b"\n")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
