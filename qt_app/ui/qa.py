"""Deterministic native-widget screenshots; does not build or install anything."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from PySide6.QtWidgets import QApplication
from packager.store import AppStore
from .mainwindow import MainWindow


def capture_screenshots(output_dir: Path):
    """Call with an existing QApplication and Windows QPA (not offscreen)."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('Create QApplication before capturing screenshots')
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='packager-ui-qa-') as temp:
        store = AppStore(Path(temp))
        window = MainWindow(store)
        window.show()

        def capture(widget, name):
            app.processEvents()
            widget.grab().save(str(output_dir / name))

        capture(window, 'workspace-empty.png')
        window.load_example()
        window.toast.hide()
        capture(window, 'workspace-populated.png')
        window.advanced_button.setChecked(True)
        app.processEvents()
        window.workspace_scroll.verticalScrollBar().setValue(window.workspace_scroll.verticalScrollBar().maximum())
        capture(window, 'workspace-output-options.png')
        window.advanced_button.setChecked(False)
        window.workspace_scroll.verticalScrollBar().setValue(0)
        window.navigate(2)
        capture(window, 'settings.png')
        window.navigate(1)
        capture(window, 'history-empty.png')
        record = {'id': 'ui-screenshot-demo', 'date': datetime.now(timezone.utc).isoformat(),
                  'name': '桌面软件套装-1.0.0-Bundle.exe（演示记录）', 'status': 'cancelled',
                  'config': window.current_config().to_dict(), 'output_file': '', 'artifact_dir': '',
                  'logs': [{'time': '12:00:00', 'message': '仅截图示例记录；未执行真实构建或安装。'}],
                  'error': '仅截图演示，未执行真实构建。'}
        store.add_history(record)
        window.history = store.load_history()
        window.render_history()
        capture(window, 'history-populated.png')
        window.show_record(record)
        capture(window.detail_dialog, 'history-detail.png')
        window.detail_dialog.close()
        window.close()
        window.deleteLater()
        app.processEvents()
