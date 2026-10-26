import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()     # required for PyInstaller builds on Windows
    from SimpleNesting.gui.app import run
    run()


if __name__ == "__main__":
    main()
