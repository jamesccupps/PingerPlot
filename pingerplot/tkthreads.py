"""Tk objects must be finalised on the main thread.

tkinter's Variable and Image have finalisers (``__del__``) that call into Tcl
to unset the variable or delete the image. Python's cyclic garbage collector
runs in whichever thread happens to allocate when a generation fills up --
here, as often as not, a probe or DNS worker. A Tcl call from a thread other
than Tk's waits for the main loop to take it, for about a second, and then
fails with "main thread is not in main loop".

Most of the window's Tk objects sit in reference cycles (a widget and its
parent refer to each other), so whenever any of them becomes garbage -- a
closed Edit dialog's form variables, the theme's images in a test -- the
collector decides which thread finalises them. Measured: one torn-down window
collected on a worker thread took 68 s, a second per image and variable; a
closed dialog's twenty variables would stall a probe round for twenty seconds.

``install()`` wraps both finalisers: off the main thread, the object is kept
on a queue instead (resurrecting it is allowed, and Python still runs its
original finaliser exactly once: here, when ``drain()`` does). The window's
refresh timer drains the queue on the main thread.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk

_pending: "queue.SimpleQueue" = queue.SimpleQueue()
_installed = False


def install() -> None:
    """Idempotent; call before the first Tk object is made."""
    global _installed
    if _installed:
        return
    _installed = True
    main = threading.main_thread()
    for cls in (tk.Variable, tk.Image):
        original = cls.__dict__.get("__del__")
        if original is None:
            continue

        def __del__(self, _original=original):
            if threading.current_thread() is main:
                _original(self)
            else:
                _pending.put((_original, self))
        cls.__del__ = __del__


def drain() -> int:
    """Run the finalisers queued by other threads. Main thread only."""
    n = 0
    while True:
        try:
            fn, obj = _pending.get_nowait()
        except queue.Empty:
            return n
        try:
            fn(obj)
        except (tk.TclError, RuntimeError):
            pass        # its interpreter is gone already: nothing left to undo
        n += 1


def pending() -> int:
    return _pending.qsize()
