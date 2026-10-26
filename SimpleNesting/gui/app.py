"""Main window: File menu (projects), tabs, RUN NESTING button, status bar, nesting controller glue."""
from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMessageBox, QProgressDialog,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import project, strings as S
from ..controller import NestingController
from ..models import MAX_TIME_LIMIT_S, MaterialResult, NestOptions, fingerprint
from ..nesting.engine import available_engines
from ..settings import AUTOSAVE_FILE, LOG_FILE, Settings, setup_logging
from . import theme
from .edit_tab import EditTab
from .report_tab import ReportTab
from .setup_tab import SetupTab
from .state import AppModel
from .widgets import Segmented, button, label

log = logging.getLogger(__name__)
FILTER = f"SimpleNesting projects (*{project.EXT});;All files (*)"


class _OpenWorker(QObject):
    """Reads the DXF files of a project in a background thread."""
    progress = Signal(int, int)
    done = Signal(object, object)       # materials, LoadReport
    failed = Signal(str)

    def __init__(self, data: dict, project_dir: Path | None, units: str):
        super().__init__()
        self.data, self.project_dir, self.units = data, project_dir, units

    def run(self) -> None:
        try:
            mats, report = project.materials_from_dict(self.data, self.project_dir, self.units,
                                                       lambda d, t: self.progress.emit(d, t))
            self.done.emit(mats, report)
        except Exception:                                   # noqa: BLE001 - shown to the user
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.model = AppModel()
        self.project_path: Path | None = None
        self._dirty = False
        self._saved_snapshot: str | None = None
        self._open_ctx = None                               # (data, path, mark_dirty, dialog, thread, worker)
        self.resize(1500, 960)
        self.setMinimumSize(1280, 880)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 10, 24, 10)
        lay.setSpacing(10)

        self.tabs = Segmented([S.TAB_SETUP, S.TAB_REPORT, S.TAB_EDIT])
        self.tabs.setMinimumWidth(330)
        self.tabs.changed.connect(self.show_tab)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.tabs)
        row.addStretch(1)
        lay.addLayout(row)

        self.stack = QStackedWidget()
        self.setup = SetupTab(self.model, settings, self.set_status)
        self.report = ReportTab(settings, self.set_status, self.setup.set_output_dir)
        self.edit = EditTab(settings, self.set_status)
        self.edit.changed.connect(self._on_edited)
        self.setup.theme_changed.connect(self.apply_theme)
        self.stack.addWidget(self.setup)
        self.stack.addWidget(self.report)
        self.stack.addWidget(self.edit)
        lay.addWidget(self.stack, 1)

        self.run_btn = button("▶  RUN NESTING", "run", self.run_nesting)
        self.run_btn.setFixedHeight(48)
        lay.addWidget(self.run_btn)
        bar = QHBoxLayout()
        self.status = label(S.MSG_READY, "muted")
        bar.addWidget(self.status, 1)
        bar.addWidget(button("Error Log", "link", self.open_log))
        lay.addLayout(bar)

        self.controller = NestingController(self.on_start, self.on_progress, self.on_phase, self.on_material_done,
                                            self.on_finished)
        self.run_materials: list = []
        self.results: list[MaterialResult] = []
        self._phase: tuple[int, str, int, float] | None = None

        self._build_menu()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self.state_timer = QTimer(self)                     # stale results + unsaved-changes flag
        self.state_timer.timeout.connect(self._tick_state)
        self.state_timer.start(1000)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start(60_000)
        self.show_tab(S.TAB_SETUP)
        self._saved_snapshot = self._snapshot()
        self._update_title()

    # ---------------------------------------------------------------- menu ---
    def _action(self, text: str, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(shortcut)
        a.triggered.connect(lambda _=False: slot())
        return a

    def _build_menu(self) -> None:
        fm = self.menuBar().addMenu("&File")
        self.act_new = self._action("&New Project", self.new_project, QKeySequence.StandardKey.New)
        self.act_open = self._action("&Open Project...", self.open_project, QKeySequence.StandardKey.Open)
        self.act_save = self._action("&Save", self.save_project, QKeySequence.StandardKey.Save)
        self.act_save_as = self._action("Save &As...", self.save_project_as, QKeySequence("Ctrl+Shift+S"))
        self.act_exit = self._action("E&xit", self.close, QKeySequence("Ctrl+Q"))
        fm.addAction(self.act_new)
        fm.addAction(self.act_open)
        self.recent_menu = fm.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        fm.addSeparator()
        fm.addAction(self.act_save)
        fm.addAction(self.act_save_as)
        fm.addSeparator()
        fm.addAction(self.act_exit)

        # --- Help menu (top-level, after File) ---
        hm = self.menuBar().addMenu("&Help")
        self.act_howto = self._action("How to Use...", self.show_howto)
        self.act_about = self._action("About...", self.show_about)
        hm.addAction(self.act_howto)
        hm.addSeparator()
        hm.addAction(self.act_about)
        hm.addSeparator()
        self.act_licenses = self._action("Licenses...", self.show_licenses)
        hm.addAction(self.act_licenses)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        paths = self.settings.recent_projects
        if not paths:
            self.recent_menu.addAction("No recent projects").setEnabled(False)
            return
        for p in paths:
            exists = Path(p).is_file()
            a = self._action(Path(p).name + ("" if exists else "  (missing)"), lambda p=p: self.open_project(p))
            a.setToolTip(p)
            a.setEnabled(exists)
            self.recent_menu.addAction(a)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction(self._action("Clear List", self._clear_recent))

    def _clear_recent(self) -> None:
        self.settings.recent_projects = []
        self.settings.save()

    def show_howto(self) -> None:
        QMessageBox.information(
            self,
            S.MSG_HELP_TITLE,
            S.MSG_HELP_INTRO,
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            S.MSG_ABOUT_TITLE,
            S.MSG_ABOUT_CONTENT,
        )

    def show_licenses(self) -> None:
        QMessageBox.about(
            self,
            S.MSG_LICENSES_TITLE,
            S.MSG_LICENSES_CONTENT,
        )

    # ------------------------------------------------------------------ ui ---
    def show_tab(self, name: str) -> None:
        self.tabs.set(name)
        if name == S.TAB_REPORT:
            self.report.show_results(list(self.results), keep_selection=True)     # picks up manual edits
        elif name == S.TAB_EDIT:
            self.edit.set_data(self.results, self.model.materials)
        self.stack.setCurrentWidget({S.TAB_SETUP: self.setup, S.TAB_REPORT: self.report, S.TAB_EDIT: self.edit}[name])

    def _on_edited(self) -> None:
        self._tick_state()                                  # unsaved-changes flag in the title

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def hint_first_step(self, ms: int = 1500) -> None:
        """Fresh start: point at the first button to press, so no manual is needed.

        The highlight is skipped when the project already has materials (restored autosave,
        opened project) - then there is nothing to point at.
        """
        if self.model.materials:
            return
        self.setup.hint_add_material(ms)
        self.set_status(S.MSG_FIRST_STEP)
        QTimer.singleShot(ms, self._clear_first_step_hint)

    def _clear_first_step_hint(self) -> None:
        if not self.model.materials:
            self.set_status(S.MSG_READY)

    def apply_theme(self) -> None:
        theme.apply(QApplication.instance(), self.settings.appearance, self.settings.accent)
        self.setup.mat_list.viewport().update()
        self.setup.files_view.viewport().update()

    def open_log(self) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE)))

    def _update_title(self) -> None:
        name = self.project_path.stem if self.project_path else "Untitled"
        self.setWindowTitle(f"{'*' if self._dirty else ''}{name} - {S.APP_NAME}")

    # ------------------------------------------------------------- project ---
    def _snapshot(self) -> str:
        return project.snapshot(self.model.materials, project.settings_block(self.settings), self.results)

    def _tick_state(self) -> None:
        mats = {m.uid: m for m in self.model.materials}
        opts = self.options()
        changed = False
        kept = [r for r in self.results if r.material_uid in mats]      # a removed material loses its result
        if len(kept) != len(self.results):
            self.results, changed = kept, True
        for r in self.results:
            stale = fingerprint(mats[r.material_uid], opts) != r.fingerprint
            if stale != r.stale:
                r.stale, changed = stale, True
        if changed:
            self.report.show_results(list(self.results), keep_selection=True)
        dirty = self._snapshot() != self._saved_snapshot
        if dirty != self._dirty:
            self._dirty = dirty
            self._update_title()

    def _busy(self) -> bool:
        if self.controller.running:
            self.set_status("Stop the nesting first (Cancel).")
            return True
        return False

    def maybe_save(self) -> bool:
        """Ask about unsaved changes. False = the user cancelled."""
        self._tick_state()
        if not self._dirty:
            return True
        name = self.project_path.name if self.project_path else "Untitled"
        answer = QMessageBox.question(
            self, S.APP_NAME, f"Save changes to \"{name}\"?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self._busy() or not self.maybe_save():
            return
        self.model.materials, self.model.selected, self.model.selected_part = [], -1, -1
        self.results = []
        self.project_path = None
        self.report.show_results([])
        self.setup.refresh_all()
        self._saved_snapshot = self._snapshot()
        self._clear_autosave()
        self._tick_state()
        self._update_title()
        self.show_tab(S.TAB_SETUP)
        self.set_status("New project.")

    def save_project(self) -> bool:
        return self._write(self.project_path) if self.project_path else self.save_project_as()

    def save_project_as(self) -> bool:
        start = str(self.project_path or (Path(self.settings.last_dir or Path.home()) / f"project{project.EXT}"))
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, FILTER)
        if not path:
            return False
        if not Path(path).suffix:
            path += project.EXT
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        try:
            data = project.to_dict(self.model.materials, project.settings_block(self.settings), self.results,
                                   path.parent, max(self.model.selected, 0))
            project.write(path, data)
        except OSError as exc:
            log.exception("Cannot save project")
            QMessageBox.critical(self, S.APP_NAME, f"Cannot save the project:\n{exc}")
            return False
        self.project_path = path
        self.settings.add_recent(str(path))
        self.settings.save()
        self._saved_snapshot = self._snapshot()
        self._clear_autosave()
        self._dirty = False
        self._update_title()
        self.set_status(f"Project saved: {path}")
        return True

    def open_project(self, path: str | None = None) -> None:
        if self._busy() or not self.maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", self.settings.last_dir or "", FILTER)
        if not path:
            return
        try:
            data = project.read(path)
        except project.ProjectError as exc:
            QMessageBox.critical(self, S.APP_NAME, str(exc))
            return
        self._open_data(data, Path(path), mark_dirty=False)

    def _open_data(self, data: dict, path: Path | None, mark_dirty: bool) -> None:
        """Read the DXF files in a thread (with a progress dialog), then apply the project."""
        units = data.get("settings", {}).get("units", "mm")
        total = max(1, sum(len(m.get("parts", [])) for m in data.get("materials", [])))
        dlg = QProgressDialog("Opening project...", "", 0, total, self)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(400)
        thread, worker = QThread(self), _OpenWorker(data, path.parent if path else None, units)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Connect to methods of this window (a QObject living in the GUI thread): Qt then delivers the
        # signals in the GUI thread. A lambda would run in the worker thread and crash.
        worker.progress.connect(self._on_open_progress)
        worker.done.connect(self._on_open_done)
        worker.failed.connect(self._on_open_failed)
        self._open_ctx = (data, path, mark_dirty, dlg, thread, worker)
        thread.start()

    def _on_open_progress(self, done: int, total: int) -> None:
        if self._open_ctx:
            self._open_ctx[3].setValue(done)

    def _finish_open_thread(self):
        data, path, mark_dirty, dlg, thread, worker = self._open_ctx
        thread.quit()
        thread.wait()
        dlg.close()
        self._open_ctx = None
        return data, path, mark_dirty

    def _on_open_done(self, mats, report) -> None:
        data, path, mark_dirty = self._finish_open_thread()
        self._apply_loaded(data, mats, report, path, mark_dirty)

    def _on_open_failed(self, trace: str) -> None:
        self._finish_open_thread()
        log.error("Opening the project failed:\n%s", trace)
        QMessageBox.critical(self, S.APP_NAME, "Cannot open the project:\n\n" + trace.strip().splitlines()[-1])

    def load_project_data(self, data: dict, path: Path | None, mark_dirty: bool = False) -> None:
        """Synchronous variant (used by tests and tools)."""
        mats, report = project.materials_from_dict(data, path.parent if path else None,
                                                   data.get("settings", {}).get("units", "mm"))
        self._apply_loaded(data, mats, report, path, mark_dirty)

    def _apply_loaded(self, data: dict, mats: list, report: project.LoadReport, path: Path | None,
                      mark_dirty: bool) -> None:
        s = self.settings
        for k, v in data.get("settings", {}).items():
            if k in project.SETTING_KEYS and v is not None:
                setattr(s, k, v)
        s.time_limit = max(1, min(int(s.time_limit), MAX_TIME_LIMIT_S))
        notes = []
        if not available_engines().get(s.engine):
            notes.append(f"Engine '{s.engine}' is not installed, using libnest2d.")
            s.engine = "libnest2d"
        opts = self.options()
        self.model.materials = mats
        sel = int(data.get("selected", 0))
        self.model.selected = sel if 0 <= sel < len(mats) else (0 if mats else -1)
        self.model.selected_part = 0
        for m in mats:
            m.update_fit(opts)
        self.results = project.results_from_dict(data, mats, opts, report)
        self.setup.sync_from_settings()
        self.setup.refresh_all()
        self.report.show_results(list(self.results))
        self.project_path = path
        self._saved_snapshot = None if mark_dirty else self._snapshot()
        if path and not mark_dirty:
            s.add_recent(str(path))
            s.last_dir = str(path.parent)
            s.save()
        name = path.name if path else "unsaved work"
        msg = f"Restored {name}." if mark_dirty else f"Opened {name}."
        if report.missing:
            msg += f" {len(report.missing)} file(s) missing: use Locate Missing."
        if report.changed:
            msg += f" {len(report.changed)} file(s) changed since the project was saved."
        if any(r.stale for r in self.results):
            msg += " Results are out of date."
        if report.skipped_results:
            msg += f" {report.skipped_results} result(s) could not be restored."
        self.set_status(" ".join([msg] + notes))
        self.show_tab(S.TAB_SETUP)
        self._tick_state()
        self._update_title()

    # ------------------------------------------------------------ autosave ---
    def autosave(self) -> None:
        self._tick_state()
        if not self._dirty or not self.model.materials:
            return
        try:
            payload = {"project_path": str(self.project_path) if self.project_path else None,
                       "project": project.to_dict(self.model.materials, project.settings_block(self.settings),
                                                  self.results, None, max(self.model.selected, 0))}
            AUTOSAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            project.write(AUTOSAVE_FILE, payload)
        except OSError:
            log.exception("Autosave failed")

    def _clear_autosave(self) -> None:
        try:
            AUTOSAVE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def restore_autosave(self) -> None:
        """After a crash: offer the work that was not saved."""
        if not AUTOSAVE_FILE.is_file():
            return
        try:
            payload = json.loads(AUTOSAVE_FILE.read_text(encoding="utf-8"))
            data = payload["project"]
        except (OSError, ValueError, KeyError):
            self._clear_autosave()
            return
        when = data.get("saved_at", "earlier")
        name = Path(payload["project_path"]).name if payload.get("project_path") else "an untitled project"
        answer = QMessageBox.question(self, S.APP_NAME, f"SimpleNesting found unsaved work on {name} from {when}.\nRestore it?")
        if answer == QMessageBox.StandardButton.Yes:
            path = Path(payload["project_path"]) if payload.get("project_path") else None
            self._open_data(data, path, mark_dirty=True)
        else:
            self._clear_autosave()

    # ------------------------------------------------------------- nesting ---
    def options(self) -> NestOptions:
        return self.settings.to_options()

    def run_nesting(self) -> None:
        if self.controller.running:
            self.controller.cancel()
            self.set_status("Cancelling...")
            return
        if any(r.edited for r in self.results) and QMessageBox.question(
                self, S.APP_NAME, "Running the nesting again replaces the results, including your manual edits.\n"
                                  "Continue?") != QMessageBox.StandardButton.Yes:
            return
        opts = self.options()
        mats = []
        for m in self.model.materials:
            m.update_fit(opts)
            if any(p.nestable for p in m.parts):
                if m.sheet[0] - 2 * m.margin <= 0 or m.sheet[1] - 2 * m.margin <= 0:
                    QMessageBox.warning(self, S.APP_NAME, f"{m.label}: the margin is larger than the sheet.")
                    return
                mats.append(m)
        if not mats:
            self.set_status(S.MSG_NOTHING_TO_NEST)
            return
        self.run_materials, self.results = mats, []
        self.report.show_results([])
        self.run_btn.setText("■  CANCEL")
        self.set_status(S.MSG_NESTING)
        self.controller.start(mats, opts)               # materials are pickled = a snapshot

    def _tick(self) -> None:
        if not self.controller.running:
            return
        self.controller.poll()
        if self.controller.running and self._phase:     # long solver run: live elapsed timer
            i, text, budget, t0 = self._phase
            self.set_status(f"Nesting {self.run_materials[i].label} ({i + 1}/{len(self.run_materials)}): "
                            f"{text} - {time.monotonic() - t0:.0f} s of up to {budget} s")

    def on_start(self, i: int, label_text: str) -> None:
        self._phase = None
        self.set_status(f"Nesting {label_text} ({i + 1}/{len(self.run_materials)})...")

    def on_progress(self, i: int, done: int, total: int) -> None:
        if not self._phase:
            self.set_status(f"Nesting {self.run_materials[i].label} ({i + 1}/{len(self.run_materials)}): "
                            f"{done}/{total} parts placed")

    def on_phase(self, i: int, text: str, budget_s: int) -> None:
        self._phase = (i, text, budget_s, time.monotonic())

    def on_material_done(self, i: int, result: MaterialResult) -> None:
        self._phase = None
        self.results.append(result)
        self.report.show_results(list(self.results), keep_selection=True)

    def on_finished(self, kind: str, detail: str) -> None:
        self._phase = None
        self.run_btn.setText("▶  RUN NESTING")
        if kind == "error":
            self.set_status("Nesting failed. See Error Log.")
            QMessageBox.critical(self, S.APP_NAME, "Nesting failed:\n\n" + detail.strip().splitlines()[-1])
            return
        if kind == "cancelled":
            self.set_status(S.MSG_CANCELLED)
        else:
            sheets = sum(len(r.sheets) for r in self.results)
            files = sum(len(r.dxf_files) for r in self.results)
            extra = f" {files} DXF file(s) written." if files else ""
            self.set_status(f"{S.MSG_DONE} {len(self.results)} material(s), {sheets} sheet(s).{extra}")
        warnings = [w for r in self.results for w in r.warnings]
        if warnings:
            self.set_status(f"Warning: {len(warnings)} layout problem(s) found. Do not cut before checking.")
            QMessageBox.warning(self, S.APP_NAME, "The final layout check found problems:\n\n" + "\n".join(warnings[:8]))
        if self.results:
            self.show_tab(S.TAB_REPORT)
            if self.settings.auto_save_report and kind == "done":
                self.report.save_report(auto=True)

    def closeEvent(self, event):
        if not self.maybe_save():
            event.ignore()
            return
        self._clear_autosave()
        self.controller.kill()
        self.settings.save()
        super().closeEvent(event)


def run() -> None:
    setup_logging()
    settings = Settings.load()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(S.APP_NAME)
    theme.apply(app, settings.appearance, settings.accent)
    win = MainWindow(settings)
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "colorSchemeChanged"):                # follow the OS theme live in "System" mode
        hints.colorSchemeChanged.connect(lambda *_: settings.appearance == "System" and win.apply_theme())
    win.show()
    QTimer.singleShot(400, win.restore_autosave)
    QTimer.singleShot(800, win.hint_first_step)     # after the autosave dialog: it may bring materials
    sys.exit(app.exec())
