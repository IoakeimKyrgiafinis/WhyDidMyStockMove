"""Give freed memory back to the operating system.

On Linux, glibc keeps memory that Python, pandas and Arrow have freed, so a process that reads
a few large files grows and stays grown: in a 512 MB container the API sat at 517 MB after
deleting every table it had read. `malloc_trim` returns those pages. Elsewhere this does
nothing but collect garbage.
"""

from __future__ import annotations

import ctypes
import gc
import sys
import threading
import time

_libc: ctypes.CDLL | None = None
if sys.platform.startswith("linux"):
    try:
        _libc = ctypes.CDLL("libc.so.6")
    except OSError:  # musl or another libc without malloc_trim
        _libc = None

_lock = threading.Lock()
_last = 0.0


def release_memory() -> None:
    """Collect garbage and return free heap pages to the OS."""
    gc.collect()
    if _libc is not None:
        _libc.malloc_trim(0)


def release_memory_every(seconds: float) -> None:
    """`release_memory`, at most once per `seconds` across threads (cheap to call often)."""
    global _last
    now = time.monotonic()
    with _lock:
        if now - _last < seconds:
            return
        _last = now
    release_memory()
