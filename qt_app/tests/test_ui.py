"""Behavioral native Qt tests; no source executable is ever launched."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
import threading
from unittest.mock import patch
from pathlib import Path
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
from PySide6.QtTest import QTest


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        from ui.mainwindow import MainWindow
        from packager.store import AppStore
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(AppStore(Path(self.temp.name)))

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def test_runtime_reordering_keeps_parameters_attached_to_installer(self):
        self.window.add_runtime_paths(['first.exe', 'second.msi'])
        self.window.runtime_rows[0].params.setText('/quiet /norestart')
        self.window.move_runtime(0, 1)
        config = self.window.current_config()
        self.assertEqual([item.path.name for item in config.runtimes], ['second.msi', 'first.exe'])
        self.assertEqual(config.runtimes[1].params, '/quiet /norestart')
        self.window.remove_runtime(0)
        self.assertEqual(self.window.current_config().runtimes[0].path.name, 'first.exe')

    def test_configuration_roundtrip_preserves_optional_roles_and_runtime_params(self):
        from packager.models import BundleConfig, RuntimeInstaller
        config = BundleConfig(paths={'C': 'D:/Feature', 'B': 'D:/Engine'}, output='D:/Out',
                              runtimes=[RuntimeInstaller(Path('D:/VC.exe'), '/quiet')],
                              entries={'C': 'Feature.exe'})
        self.window.apply_config(config)
        restored = self.window.current_config()
        self.assertEqual(restored.paths.get('A', ''), '')
        self.assertEqual(restored.paths['B'], 'D:/Engine')
        self.assertEqual(restored.runtimes[0].params, '/quiet')
        self.assertEqual(restored.entries, {'C': 'Feature.exe'})

    def test_build_lock_disables_browsing_and_settings(self):
        self.window._lock_build(True)
        try:
            controls = self.window.path_inputs['C'].parentWidget().findChildren(QPushButton)
            self.assertTrue(controls)
            self.assertTrue(all(not control.isEnabled() for control in controls))
            self.assertFalse(self.window.pages.widget(2).isEnabled())
        finally:
            self.window._lock_build(False)

    def test_preflight_file_scan_does_not_block_qt_event_loop(self):
        from packager.validation import ValidationError
        started = threading.Event()
        release = threading.Event()
        def slow_disk_scan(config, *, check_files):
            self.assertFalse(check_files)
            started.set()
            release.wait(3)
            raise ValidationError('test validation error')
        timer_fired = []
        with patch('ui.mainwindow.validate_config', slow_disk_scan), patch('ui.mainwindow.QMessageBox.warning'):
            self.window.start_build()
            QTimer.singleShot(10, lambda: timer_fired.append(True))
            for _ in range(30):
                QTest.qWait(10)
                if started.is_set() and timer_fired:
                    break
            try:
                self.assertTrue(started.is_set())
                self.assertTrue(timer_fired)
                self.assertTrue(self.window.building)
            finally:
                release.set()
                for _ in range(100):
                    QTest.qWait(10)
                    if not self.window.building:
                        break
            self.assertFalse(self.window.building)

    def test_success_details_open_after_unlock_so_configuration_can_be_reused(self):
        from packager.models import BuildResult
        self.window.active_config = self.window.current_config()
        self.window._lock_build(True)
        result = BuildResult(Path(self.temp.name) / 'bundle.exe', Path(self.temp.name),
                             Path(self.temp.name) / 'manifest.json', 'test-build', ['C'], 0)
        try:
            self.window.build_succeeded(result)
            self.assertIsNone(self.window.detail_dialog)
            self.window.thread_finished()
            reuse = next(b for b in self.window.detail_dialog.findChildren(QPushButton) if b.text() == '使用此配置')
            self.assertTrue(reuse.isEnabled())
            self.window.detail_dialog.close()
        finally:
            self.window._lock_build(False)


if __name__ == '__main__':
    unittest.main()
