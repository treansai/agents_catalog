"""Écriture atomique de fichiers JSON : fichier temporaire 0600, fsync, puis renommage."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import Any


def dump_json(value: Any) -> str:
    """Équivalent de `JSON.stringify(value, null, 2)` suivi d'un saut de ligne."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def write_atomically(target: Path, payload: str, *, sync_directory: bool) -> None:
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        data = payload.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, target)
        if sync_directory:
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
