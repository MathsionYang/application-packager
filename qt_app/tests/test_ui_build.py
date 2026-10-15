"""End-to-end Qt worker -> ISCC -> persistent history; never installs software."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import shutil
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from packager.models import BundleConfig, DEFAULT_COMPILER
from packager.store import AppStore
from ui.mainwindow import MainWindow


@pytest.mark.skipif(not Path(DEFAULT_COMPILER).is_file(), reason='Inno compiler unavailable')
def test_gui_builds_real_bundle_in_worker_and_records_result(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / '发布目录'
    source.mkdir()
    shutil.copy2(Path(DEFAULT_COMPILER).parent / 'Examples' / 'MyProg.exe', source / 'Feature.exe')
    (source / 'data.txt').write_text('fixture data', encoding='utf-8')
    store = AppStore(tmp_path / 'state')
    window = MainWindow(store)
    window.apply_config(BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), name='界面构建验证'))
    window.start_build()
    deadline = time.monotonic() + 90
    while window.building and time.monotonic() < deadline:
        QTest.qWait(20)
        app.processEvents()
    try:
        assert not window.building, 'background build exceeded 90 seconds'
        history = store.load_history()
        assert history and history[0]['status'] == 'success', window.build_logs
        assert Path(history[0]['output_file']).read_bytes()[:2] == b'MZ'
        assert Path(history[0]['artifact_dir']).is_dir()
        assert history[0]['config']['paths']['C'] == str(source)
        assert window.build_progress.value() == 100
        assert any('ISCC' in item['message'] for item in history[0]['logs'])
    finally:
        if window.building:
            window.cancel_event.set()
            while window.building:
                QTest.qWait(20)
        window.close()
        window.deleteLater()
        app.processEvents()
