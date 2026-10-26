"""PyInstaller build script for SimpleNesting ready-to-run program.

Builds a --onedir archive (LGPL compliance: PySide6 and python-libnest2d are
LGPL, so they must stay replaceable - never --onefile).

Usage:
    pip install pyinstaller
    python build.py --clean

Result: dist/simplenesting/
"""
import shutil
import subprocess
import sys
from pathlib import Path

from PyInstaller import __version__
from PyInstaller.utils.hooks import collect_submodules


def main() -> None:
    root = Path(__file__).resolve().parent
    dist_dir = root / "dist"
    work_dir = root / "build"
    out_dir = dist_dir / "simplenesting"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--name", "simplenesting",
        "--windowed",
        "--onedir",
        "--clean",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(root),
        "--paths", str(root),
        "--add-data", f"{root / 'SimpleNesting'}:SimpleNesting",
        "--add-data", f"{root / 'LICENSE'}:.",
        "--hidden-import", "SimpleNesting",
        "--hidden-import", "SimpleNesting.nesting.libnest_engine",
        "--hidden-import", "SimpleNesting.nesting.spyrrow_engine",
        "--hidden-import", "SimpleNesting.geometry.dxf_loader",
        "--collect-submodules", "SimpleNesting",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "wxPython",
        "--exclude-module", "tkinter",
        str(root / "main.py"),
    ]

    print(f"PyInstaller version: {__version__}")
    print("Building SimpleNesting...")
    subprocess.run(args, check=True, cwd=str(root))

    # Copy third-party licenses into the bundle (required by LGPL)
    licenses_dir = root / "licenses"
    bundle_licenses = out_dir / "licenses"
    if licenses_dir.exists():
        shutil.copytree(licenses_dir, bundle_licenses, dirs_exist_ok=True)

    print(f"\nBuild complete: {out_dir}")
    print("Ready-to-run program is in dist/simplenesting/")
    print("Start it from the folder: simplenesting (Windows: simplenesting.exe)")


if __name__ == "__main__":
    main()
