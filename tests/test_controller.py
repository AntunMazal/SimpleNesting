"""Real spawn of the worker process: progress, completion and cancellation."""
import multiprocessing
import tempfile
import time
from pathlib import Path

from SimpleNesting.controller import NestingController
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions
from tests.test_core import make_plate


def run(material, options, cancel_after=None):
    ev = []
    c = NestingController(lambda i, l: ev.append(("start", l)),
                          lambda i, d, t: ev.append(("progress", d, t)),
                          lambda i, text, budget: ev.append(("phase", text)),
                          lambda i, r: ev.append(("result", r.nested, len(r.sheets), len(r.dxf_files))),
                          lambda k, d: ev.append(("finished", k)))
    c.start([material], options)
    t0 = time.time()
    while c.running and time.time() - t0 < 60:
        c.poll()
        if cancel_after is not None and time.time() - t0 > cancel_after:
            c.cancel()
            cancel_after = None
        time.sleep(0.05)
    return ev


def test_controller():
    tmp = Path(tempfile.mkdtemp())
    make_plate(tmp / "a.dxf", 300, 200, hole_r=30)
    part = load_part(str(tmp / "a.dxf"))
    part.qty = 40
    mat = Material(name="X", thickness=2, parts=[part])
    ev = run(mat, NestOptions(output_dir=str(tmp / "out")))
    assert ev[0] == ("start", "2mm X"), ev
    assert ("finished", "done") in ev and ev[-1] == ("finished", "done"), ev
    result = [e for e in ev if e[0] == "result"][0]
    assert result[1] == 40 and result[3] >= 1, result
    ev = run(mat, NestOptions(), cancel_after=0.0)
    assert ev[-1][0] == "finished" and ev[-1][1] in ("cancelled", "done"), ev




def slow_worker(materials, options, q, stop_event):
    """Pretends to be a solver that never returns."""
    q.put(("start", 0, "stuck"))
    q.put(("phase", 0, "sheet 1: placing 10 parts", 1))      # budget 1 s
    time.sleep(120)


def test_watchdog():
    from SimpleNesting import controller as ctl
    ctl.WATCHDOG_GRACE_S = 1
    original = ctl._worker
    ctl._worker = slow_worker
    try:
        ev = run(Material(name="X", thickness=2), NestOptions())
    finally:
        ctl._worker = original
    assert ev[-1][0] == "finished" and ev[-1][1] == "error", ev
    assert not any(e[0] == "result" for e in ev)


def test_time_limit_cap():
    from SimpleNesting.models import MAX_TIME_LIMIT_S
    from SimpleNesting.settings import Settings
    assert Settings(time_limit=999).to_options().time_limit_s == MAX_TIME_LIMIT_S == 30
    assert Settings(time_limit=0).to_options().time_limit_s == 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    test_controller()
    test_time_limit_cap()
    test_watchdog()
    print("controller OK")
