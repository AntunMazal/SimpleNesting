"""Runs nesting in a separate PROCESS (not a thread).

Why a process: the C++ nester can run for a long time and may hold the GIL, which would freeze
Tk. A child process keeps the GUI responsive, makes Cancel reliable (terminate as a fallback)
and a crash inside C++ cannot take the whole application down.

Materials are nested one after another on purpose (no parallelism yet).
The GUI calls poll() from Tk's after() loop; no Tk object is ever touched from the worker.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import time
import traceback
from typing import Callable

from .models import Material, MaterialResult, NestOptions

log = logging.getLogger(__name__)
WATCHDOG_GRACE_S = 15                   # a solver may overrun its budget by this much before it is stopped
_CTX = mp.get_context("spawn")          # same behaviour on Windows, Linux and PyInstaller builds


def _worker(materials: list[Material], options: NestOptions, q, stop_event) -> None:
    """Child process entry point. Messages are small tuples put on the queue."""
    try:
        from .export.dxf_exporter import export_material
        from .nesting.engine import nest_material

        for i, mat in enumerate(materials):
            if stop_event.is_set():
                q.put(("cancelled",))
                return
            q.put(("start", i, mat.label))
            last = [0.0]

            def on_progress(done: int, total: int, i=i) -> None:
                now = time.monotonic()
                if now - last[0] > 0.1 or done == total:
                    last[0] = now
                    q.put(("progress", i, done, total))

            def on_phase(text: str, budget_s: int, i=i) -> None:
                q.put(("phase", i, text, budget_s))

            result = nest_material(mat, options, on_progress, stop_event.is_set, on_phase)
            if options.output_dir and result.sheets:
                result.dxf_files = export_material(mat, result, options.output_dir)
            q.put(("material_done", i, result))
            if result.cancelled:
                q.put(("cancelled",))
                return
        q.put(("done",))
    except BaseException:                       # noqa: BLE001 - report everything to the GUI
        q.put(("error", traceback.format_exc()))


class NestingController:
    def __init__(self, on_start: Callable[[int, str], None], on_progress: Callable[[int, int, int], None],
                 on_phase: Callable[[int, str, int], None],
                 on_material_done: Callable[[int, MaterialResult], None],
                 on_finished: Callable[[str, str], None]):
        """on_finished(kind, detail) with kind in 'done' | 'cancelled' | 'error'."""
        self._cb = (on_start, on_progress, on_phase, on_material_done, on_finished)
        self._proc = None
        self._queue = None
        self._stop = None
        self._cancel_at: float | None = None
        self._phase_deadline: float | None = None       # watchdog for long solver runs
        self._phase_budget = 0

    @property
    def running(self) -> bool:
        return self._proc is not None

    def start(self, materials: list[Material], options: NestOptions) -> None:
        if self.running:
            return
        self._queue = _CTX.Queue()
        self._stop = _CTX.Event()
        self._cancel_at = None
        self._proc = _CTX.Process(target=_worker, args=(materials, options, self._queue, self._stop), daemon=True)
        self._proc.start()

    def cancel(self) -> None:
        if self.running and self._stop is not None:
            self._stop.set()
            self._cancel_at = time.monotonic()

    def kill(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
        self._reset()

    def _reset(self) -> None:
        self._proc = self._queue = self._stop = self._cancel_at = self._phase_deadline = None

    def poll(self) -> None:
        """Drain the queue; call every ~100 ms from the GUI thread."""
        if not self.running:
            return
        on_start, on_progress, on_phase, on_done, on_finished = self._cb
        finished: tuple[str, str] | None = None
        while True:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "start":
                self._phase_deadline = None
                on_start(msg[1], msg[2])
            elif kind == "progress":
                self._phase_deadline = None
                on_progress(msg[1], msg[2], msg[3])
            elif kind == "phase":
                self._phase_budget = msg[3]
                self._phase_deadline = time.monotonic() + msg[3] + WATCHDOG_GRACE_S
                on_phase(msg[1], msg[2], msg[3])
            elif kind == "material_done":
                self._phase_deadline = None
                on_done(msg[1], msg[2])
            elif kind in ("done", "cancelled"):
                finished = (kind, "")
            elif kind == "error":
                log.error("Nesting worker failed:\n%s", msg[1])
                finished = ("error", msg[1])
        if finished is None and not self._proc.is_alive() and self._queue.empty():
            code = self._proc.exitcode
            if self._cancel_at is not None:
                finished = ("cancelled", "")
            else:
                finished = ("error", f"Nesting process ended unexpectedly (exit code {code}).")
                log.error(finished[1])
        if finished is None and self._phase_deadline and time.monotonic() > self._phase_deadline:
            self._proc.terminate()                  # the solver is stuck far beyond its time limit
            finished = ("error", f"The nesting engine did not finish within its time limit ({self._phase_budget} s). "
                                 "Try fewer rotation angles, a shorter time limit or the libnest2d engine.")
            log.error(finished[1])
        if finished is None and self._cancel_at is not None and time.monotonic() - self._cancel_at > 3.0:
            self._proc.terminate()              # nester ignored the stop request
        if finished is not None:
            if self._proc.is_alive():
                self._proc.join(timeout=1.0)
            self._reset()
            on_finished(*finished)
