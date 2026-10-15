"""Native Qt application entry point and non-installing render checks."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


APP_NAME = "DesktopPackager"
APP_VERSION = "1.0.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desktop software installation bundle workbench")
    parser.add_argument("--data-dir", type=Path, help="Override the application data directory")
    parser.add_argument("--smoke-test", action="store_true", help="Render the workbench and exit without building or installing")
    parser.add_argument("--screenshot-dir", type=Path, help="Save workbench.png in this directory and exit")
    args = parser.parse_args(argv)
    render_only = args.smoke_test or args.screenshot_dir is not None
    if args.screenshot_dir is not None and sys.platform == "win32":
        # Native Windows rendering preserves installed Chinese font fallback.
        os.environ["QT_QPA_PLATFORM"] = "windows"
    elif args.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from packager.store import AppStore
    from ui.mainwindow import MainWindow

    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("桌面软件安装包工坊")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("DesktopPackager")
    app.setStyle("Fusion")
    available = set(QFontDatabase.families())
    families = [name for name in ("Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC") if name in available]
    font = QFont()
    if families:
        font.setFamilies(families)
    font.setPointSize(10)
    app.setFont(font)

    store = AppStore(args.data_dir.resolve() if args.data_dir else None)
    window = MainWindow(store)
    if window.width() < 1000 or window.height() < 700:
        window.resize(1280, 900)
    window.show()

    if render_only:
        def finish_render() -> None:
            try:
                if args.screenshot_dir is not None:
                    target = args.screenshot_dir.resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(target / "workbench.png"), "PNG"):
                        raise OSError("Could not save workbench.png")
            except Exception as exc:
                print(f"Render check failed: {exc}", file=sys.stderr)
                app.exit(1)
            else:
                app.exit(0)

        QTimer.singleShot(300, finish_render)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
