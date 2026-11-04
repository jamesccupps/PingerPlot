"""A Tk variable or image that dies on a worker thread must not stall it.

The 1.4 window keeps ~40 theme images and ~20 form variables, most of them in
reference cycles, so the cyclic garbage collector -- which runs in whichever
thread is allocating, often a probe worker -- decides where they're finalised.
Their finalisers call Tcl, and a Tcl call off Tk's thread waits about a second
for the main loop before failing. Measured before the fix: one torn-down
window collected on a worker thread took 68 s; the suite went from 28 s to
236 s with "main thread is not in main loop" all over it.
"""
import threading
import time

import pytest

from pingerplot import tkthreads

tk = pytest.importorskip("tkinter")


@pytest.fixture
def installed():
    tkthreads.install()
    tkthreads.drain()


def _die_on_a_worker(obj_holder):
    took = {}

    def worker():
        t0 = time.monotonic()
        obj_holder.clear()           # the last reference goes on this thread
        took["s"] = time.monotonic() - t0
    th = threading.Thread(target=worker)
    th.start()
    th.join(10)
    return took["s"]


def test_a_variable_dying_on_a_worker_is_finalised_on_the_main_thread(tk_root, installed):
    var = tk.StringVar(master=tk_root, value="x")
    name = str(var)
    holder = [var]
    del var
    took = _die_on_a_worker(holder)
    assert took < 0.5, f"the worker waited {took:.1f} s for Tk"
    assert tk_root.getboolean(tk_root.tk.call("info", "exists", name)), "unset off the main thread"
    assert tkthreads.drain() >= 1
    assert not tk_root.getboolean(tk_root.tk.call("info", "exists", name)), "never unset"


def test_an_image_dying_on_a_worker_is_deleted_on_the_main_thread(tk_root, installed):
    img = tk.PhotoImage(master=tk_root, width=4, height=4)
    name = str(img)
    holder = [img]
    del img
    took = _die_on_a_worker(holder)
    assert took < 0.5, f"the worker waited {took:.1f} s for Tk"
    assert name in tk_root.tk.splitlist(tk_root.tk.call("image", "names"))
    tkthreads.drain()
    assert name not in tk_root.tk.splitlist(tk_root.tk.call("image", "names"))


def test_on_the_main_thread_nothing_is_deferred(tk_root, installed):
    var = tk.StringVar(master=tk_root, value="y")
    name = str(var)
    del var
    assert tkthreads.pending() == 0
    assert not tk_root.getboolean(tk_root.tk.call("info", "exists", name))
