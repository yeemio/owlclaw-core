"""Progress indicator: show spinner after a delay for long-running CLI operations."""

import sys
import threading
from contextlib import contextmanager


@contextmanager
def progress_after(seconds: float, message: str = "Working..."):
    """Context manager that shows a spinner after `seconds` if the block is still running.

    Use around long-running work (e.g. subprocess, many DB calls). The spinner is
    cleared when the block exits.
    """
    done = threading.Event()
    shown = threading.Event()

    def _spinner():
        if done.wait(timeout=seconds):
            return
        shown.set()
        chars = ["|", "/", "-", "\\"]
        i = 0
        while not done.is_set():
            try:
                sys.stdout.write(f"\r  {chars[i % 4]} {message}")
                sys.stdout.flush()
            except (OSError, BrokenPipeError):
                break
            i += 1
            done.wait(timeout=0.2)
        if shown.is_set():
            try:
                sys.stdout.write("\r" + " " * (len(message) + 6) + "\r")
                sys.stdout.flush()
            except (OSError, BrokenPipeError):
                pass

    t = threading.Thread(target=_spinner, daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join(timeout=1.0)
