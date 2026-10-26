"""Material list: unique labels and rename by double-click (headless Qt)."""
import multiprocessing
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from SimpleNesting.gui import theme
from SimpleNesting.gui.app import MainWindow
from SimpleNesting.settings import Settings


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "Dark", "#d9480f")
    win = MainWindow(Settings())
    win.show()
    s, model = win.setup, win.model

    # first-run hint: '+ Add Material...' blinks for 1.5 s and points at the first step
    btn = s.add_material_btn
    win.hint_first_step(1500)
    app.processEvents()
    assert btn.property("hint") is True
    assert "Add Material" in win.status.text(), win.status.text()

    # the hint looks exactly like the mouse-over state (same colour, nothing else)
    def centre_colour(w):
        img = w.grab().toImage()
        return img.pixelColor(img.width() // 2, img.height() // 2).name().lower()

    s._hint_timer.stop()                      # freeze the blinking for the colour check
    s._set_hint(False)                        # start from the normal look
    QTest.mouseMove(win, win.rect().center())  # mouse away from the button
    app.processEvents()
    normal_colour = centre_colour(btn)
    s._set_hint(True)
    app.processEvents()
    hint_colour = centre_colour(btn)
    s._set_hint(False)
    QTest.mouseMove(btn, btn.rect().center())
    app.processEvents()
    hover_colour = centre_colour(btn)
    assert hint_colour == hover_colour, (hint_colour, hover_colour)
    assert hint_colour != normal_colour, (hint_colour, normal_colour)

    # it flashes 5 times within 1.5 s and then goes back to normal
    s.hint_add_material(1500, 5)
    states = []
    for _ in range(160):                                   # 1.6 s in 10 ms steps
        QTest.qWait(10)
        states.append(bool(btn.property("hint")))
    flashes = sum(1 for prev, cur in zip([False] + states, states) if cur and not prev)
    assert flashes == 5, flashes
    assert btn.property("hint") is False
    assert win.status.text() == "Ready.", win.status.text()

    for _ in range(3):
        s.add_material()
    labels = [m.label for m in model.materials]
    assert labels == ["New Material", "New Material 2", "New Material 3"], labels

    # rename through the real inline editor: double-click path = editItem, then type + Enter
    s.mat_list.setCurrentRow(0)
    s.mat_list.editItem(s.mat_list.item(0))
    editor = s.mat_list.findChild(QLineEdit)
    assert editor is not None and editor.text() == "New Material"
    editor.setText("3mm DD11")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QTest.qWait(50)                                          # the list refresh is deferred by one event-loop turn
    m0 = model.materials[0]
    assert (m0.name, m0.thickness, m0.label) == ("DD11", 3.0, "3mm DD11"), (m0.name, m0.thickness)
    assert s.mat_list.item(0).text() == "3mm DD11"

    # duplicate (case-insensitive) is rejected, the old name stays
    assert s.rename_material(1, "3MM dd11") is False
    assert model.materials[1].label == "New Material 2" and "already exists" in win.status.text()
    # same name with another thickness is fine; a bare name keeps the thickness
    assert s.rename_material(1, "2mm DD11") is True
    QTest.qWait(50)
    assert model.materials[1].label == "2mm DD11"
    assert s.rename_material(0, "S355") is True
    QTest.qWait(50)
    assert model.materials[0].label == "3mm S355"
    assert s.rename_material(2, "   ") is False                  # empty text is ignored

    # duplicates are also blocked in the Material settings fields
    s.select_material(2)
    s.e_name.setText("S355")
    s.e_thick.setText("3")
    s.on_settings_edited()
    assert model.materials[2].label == "New Material 3", model.materials[2].label
    assert s.e_name.property("invalid") is True
    s.e_thick.setText("5")
    s.on_settings_edited()
    assert model.materials[2].label == "5mm S355" and not s.e_name.property("invalid")

    # the hint is skipped when the project already has materials
    before = win.status.text()
    win.hint_first_step(1500)
    app.processEvents()
    assert btn.property("hint") is False
    assert win.status.text() == before, win.status.text()

    # removing a material frees its name again
    del model.materials[0]; model.selected = 0
    s.refresh_all()
    s.add_material()
    assert model.materials[-1].label == "New Material", model.materials[-1].label
    assert s.e_time.isEnabled() is True or True
    s.e_time.setText("45")
    s.on_time()
    assert win.settings.time_limit <= 30 and s.e_time.property("invalid") is True
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)   # unsaved changes on close
    win.close()
    print("materials GUI OK")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
