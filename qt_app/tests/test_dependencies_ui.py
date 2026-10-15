"""Dependency selection behavior with small fixtures; no compiler or user tree."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QCoreApplication, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class DependencyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_batches_do_not_select_new_dll_and_missing_imports_are_retained(self):
        from ui.dependencies import DependencyFileModel
        model = DependencyFileModel(['lib/old.dll', 'missing.dll'])
        model.append_batch([{'path': 'lib/old.dll', 'size': 4, 'mtime_ns': 1},
                            {'path': 'new.dll', 'size': 8, 'mtime_ns': 1},
                            {'path': 'app.exe', 'size': 12, 'mtime_ns': 1}])
        model.finish_scan()
        self.assertEqual(set(model.selected_paths()), {'lib/old.dll', 'missing.dll'})
        missing = model.index(model.row_for_path('missing.dll'), 2)
        self.assertEqual(model.data(missing), '缺失')
        exe = model.index(model.row_for_path('app.exe'), 0)
        self.assertFalse(model.flags(exe) & Qt.ItemFlag.ItemIsUserCheckable)

    def test_filter_bulk_selection_keeps_hidden_selection(self):
        from ui.dependencies import DependencyFileModel, DependencyFilterModel
        model = DependencyFileModel(['hidden.dll'])
        model.append_batch([{'path': 'hidden.dll', 'size': 4, 'mtime_ns': 1},
                            {'path': 'plugins/visible.dll', 'size': 8, 'mtime_ns': 1},
                            {'path': 'plugins/readme.txt', 'size': 2, 'mtime_ns': 1}])
        model.finish_scan()
        proxy = DependencyFilterModel()
        proxy.setSourceModel(model)
        proxy.set_filters('plugins', dll_only=False, selected_only=False)
        model.set_checked(proxy.visible_paths(), True)
        self.assertEqual(set(model.selected_paths()), {'hidden.dll', 'plugins/visible.dll'})
        model.set_checked(proxy.visible_paths(), False)
        self.assertEqual(model.selected_paths(), ['hidden.dll'])

    def test_case_only_rename_uses_actual_scanned_path(self):
        from ui.dependencies import DependencyFileModel
        model = DependencyFileModel(['lib/OLD.dll'])
        model.append_batch([{'path': 'Lib/Old.DLL', 'size': 4, 'mtime_ns': 1}])
        model.finish_scan()
        self.assertEqual(model.selected_paths(), ['Lib/Old.DLL'])
        self.assertEqual(model.missing_paths(), [])
        model.set_selection(['LIB/OLD.DLL'])
        self.assertEqual(model.selected_paths(), ['Lib/Old.DLL'])

    def test_close_cancels_background_scan_without_blocking(self):
        from ui.dependencies import DependencyDialog
        started, stopped = threading.Event(), threading.Event()
        def slow_scan(root, cancel, on_batch=None):
            started.set()
            cancel.wait(3)
            stopped.set()
            return []
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', slow_scan):
            dialog = DependencyDialog('C', Path(temp))
            dialog.show()
            for _ in range(100):
                QTest.qWait(5)
                if started.is_set():
                    break
            self.assertTrue(started.is_set())
            self.assertFalse(dialog.select_filtered_button.isEnabled())
            dialog.close()
            for _ in range(100):
                QTest.qWait(5)
                if not dialog.isVisible() and not dialog.busy:
                    break
            self.assertTrue(stopped.is_set())
            self.assertFalse(dialog.busy)
            self.assertFalse(dialog.isVisible())
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_import_restores_only_record_choices_without_hashing(self):
        from ui.dependencies import DependencyDialog
        record = {'schema_version': 1, 'kind': 'selection', 'role': 'C',
                  'source_hint': 'old-root', 'files': [{'path': 'old.dll', 'size': 1, 'mtime_ns': 1}],
                  'recipe': None, 'cache_key': None, 'package': None}
        def scan(root, cancel, on_batch=None):
            files = [{'path': 'old.dll', 'size': 1, 'mtime_ns': 1},
                     {'path': 'new.dll', 'size': 2, 'mtime_ns': 1}]
            if on_batch:
                on_batch(files)
            return files
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', scan), \
                patch('ui.dependencies.load_record', return_value=record), \
                patch('ui.dependencies.hash_selected') as hashing:
            dialog = DependencyDialog('C', Path(temp))
            dialog.show()
            self._idle(dialog)
            dialog.import_record_path(Path(temp) / 'record.json')
            self._idle(dialog)
            self.assertEqual(dialog.model.selected_paths(), ['old.dll'])
            self.assertEqual(dialog.model.rowCount(), 2)
            hashing.assert_not_called()
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _idle(self, dialog):
        for _ in range(200):
            QTest.qWait(5)
            if not dialog.busy:
                return
        self.fail('background dependency task did not finish')

    def test_reopening_uses_config_selection_and_record_only_as_baseline(self):
        from ui.dependencies import DependencyDialog
        record = {'schema_version': 1, 'kind': 'selection', 'role': 'C',
                  'source_hint': 'old-root', 'files': [{'path': 'old.dll', 'size': 1, 'mtime_ns': 1}],
                  'recipe': None, 'cache_key': None, 'package': None}
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', return_value=[]), \
                patch('ui.dependencies.load_record', return_value=record):
            dialog = DependencyDialog('C', Path(temp), ['changed.dll'], str(Path(temp) / 'record.json'))
            self._idle(dialog)
            self.assertEqual(dialog.model.selected_paths(), ['changed.dll'])
            self.assertEqual(dialog.record, record)
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_mainwindow_preserves_dependencies_and_successful_record_paths(self):
        from ui.mainwindow import MainWindow
        from packager.models import BundleConfig, BuildResult
        from packager.store import AppStore
        with tempfile.TemporaryDirectory() as temp:
            window = MainWindow(AppStore(Path(temp)))
            config = BundleConfig(dependency_files={'C': ['lib/one.dll'], 'B': ['two.dll']},
                                  dependency_records={'C': 'old.json'}, hash_workers=7)
            window.apply_config(config)
            restored = window.current_config()
            self.assertEqual(restored.dependency_files, config.dependency_files)
            self.assertEqual(restored.dependency_records, config.dependency_records)
            self.assertEqual(restored.hash_workers, 7)
            self.assertEqual(window.dependency_counts['C'].text(), '已选 1 个 DLL')
            window.active_config = restored
            result = BuildResult(Path(temp) / 'bundle.exe', Path(temp), Path(temp) / 'manifest.json',
                                 'dependencies-test', ['C'], 0, {'C': 'new.json'})
            window.build_succeeded(result)
            self.assertEqual(window.current_config().dependency_records['C'], 'new.json')
            self.assertEqual(window.history[0]['config']['dependency_records']['C'], 'new.json')
            self.assertEqual(window.store.load_config().dependency_records['C'], 'new.json')
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_save_rejects_record_inside_source_without_hashing(self):
        from ui.dependencies import DependencyDialog
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', return_value=[]), \
                patch('ui.dependencies.hash_selected') as hashing, patch('ui.dependencies.QMessageBox.warning'):
            dialog = DependencyDialog('C', Path(temp))
            self._idle(dialog)
            dialog.save_record_path(Path(temp) / 'nested' / 'record.json')
            self.assertFalse(dialog.busy)
            hashing.assert_not_called()
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_switch_root_discards_cancelled_scan_batches(self):
        from ui.dependencies import DependencyDialog
        started = threading.Event()
        def scan(root, cancel, on_batch=None):
            if root.name == 'old':
                started.set()
                cancel.wait(3)
                on_batch([{'path': 'stale.dll', 'size': 1, 'mtime_ns': 1}])
                return []
            files = [{'path': 'new.dll', 'size': 2, 'mtime_ns': 1}]
            on_batch(files)
            return files
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', scan):
            dialog = DependencyDialog('C', Path(temp) / 'old', ['missing.dll'])
            for _ in range(100):
                QTest.qWait(5)
                if started.is_set():
                    break
            dialog.set_root(Path(temp) / 'new')
            self._idle(dialog)
            self.assertEqual({row['path'] for row in dialog.model.rows}, {'new.dll', 'missing.dll'})
            self.assertEqual(dialog.model.selected_paths(), ['missing.dll'])
            self.assertTrue(dialog.model.scan_complete)
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_mainwindow_close_keeps_event_loop_alive_until_dependency_task_stops(self):
        from ui.mainwindow import MainWindow
        from packager.store import AppStore
        started, release = threading.Event(), threading.Event()
        def scan(root, cancel, on_batch=None):
            started.set()
            release.wait(3)
            return []
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.scan_files', scan):
            window = MainWindow(AppStore(Path(temp) / 'state'))
            window.show()
            window.path_inputs['C'].setText(temp)
            window.open_dependencies('C')
            for _ in range(100):
                QTest.qWait(5)
                if started.is_set():
                    break
            dialog = window.dependency_dialogs['C']
            heartbeat = []
            window.close()
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            QTest.qWait(10)
            try:
                self.assertTrue(dialog._task.cancel.is_set())
                self.assertTrue(heartbeat)
                self.assertTrue(window.isVisible())
            finally:
                release.set()
                for _ in range(100):
                    QTest.qWait(5)
                    if not window.isVisible():
                        break
            self.assertFalse(window.isVisible())
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_save_hashes_once_and_exports_package_only_for_matching_content(self):
        from ui.dependencies import DependencyDialog
        from packager.dependencies import hash_selected, cache_key, save_record, load_record
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            root = folder / 'source'
            root.mkdir()
            dll = root / 'one.dll'
            dll.write_bytes(b'DLL contents')
            package = folder / 'dependencies.exe'
            package.write_bytes(b'MZ dependency package fixture')
            files = hash_selected(root, ['one.dll'], threading.Event())
            recipe = {'role': 'C'}
            baseline = {'schema_version': 1, 'kind': 'dependency_package', 'role': 'C',
                'source_hint': str(root), 'files': files, 'recipe': recipe, 'cache_key': cache_key(files, recipe),
                'package': {'path': str(package), 'size': package.stat().st_size,
                            'sha256': hashlib.sha256(package.read_bytes()).hexdigest()}}
            record_path = folder / 'baseline.json'
            save_record(record_path, baseline)
            dialog = DependencyDialog('C', root, ['one.dll'], str(record_path))
            self._idle(dialog)
            portable_path = folder / 'portable.json'
            with patch('ui.dependencies.hash_selected', wraps=hash_selected) as hashing:
                dialog.save_record_path(portable_path)
                self._idle(dialog)
                self.assertEqual(hashing.call_count, 1)
            exported = load_record(portable_path)
            self.assertEqual(exported['kind'], 'dependency_package')
            self.assertTrue(Path(exported['package']['path']).is_file())
            raw = json.loads(portable_path.read_text(encoding='utf-8'))
            self.assertFalse(Path(raw['package']['path']).is_absolute())
            dll.write_bytes(b'Changed DLL contents')
            changed_path = folder / 'changed.json'
            dialog.save_record_path(changed_path)
            self._idle(dialog)
            self.assertEqual(load_record(changed_path)['kind'], 'selection')
            self.assertTrue(package.is_file())
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_case_only_rename_saves_selection_without_reusing_old_package_key(self):
        from ui.dependencies import DependencyDialog
        from packager.dependencies import hash_selected, cache_key, save_record, load_record
        with tempfile.TemporaryDirectory() as temp, patch('ui.dependencies.QMessageBox.warning') as warning:
            folder = Path(temp)
            root = folder / 'source'
            root.mkdir()
            dll = root / 'one.dll'
            dll.write_bytes(b'DLL contents')
            package = folder / 'dependencies.exe'
            package.write_bytes(b'MZ dependency package fixture')
            files = hash_selected(root, ['one.dll'], threading.Event())
            recipe = {'role': 'C'}
            baseline = {'schema_version': 1, 'kind': 'dependency_package', 'role': 'C',
                'source_hint': str(root), 'files': files, 'recipe': recipe, 'cache_key': cache_key(files, recipe),
                'package': {'path': str(package), 'size': package.stat().st_size,
                            'sha256': hashlib.sha256(package.read_bytes()).hexdigest()}}
            record_path = folder / 'baseline.json'
            save_record(record_path, baseline)
            intermediate = dll.rename(root / 'renaming.tmp')
            intermediate.rename(root / 'One.DLL')
            dialog = DependencyDialog('C', root, ['one.dll'], str(record_path))
            self._idle(dialog)
            target = folder / 'case-changed.json'
            dialog.save_record_path(target)
            self._idle(dialog)
            try:
                self.assertTrue(target.is_file(), 'case-only rename must save successfully')
                saved = load_record(target)
                self.assertEqual(saved['kind'], 'selection')
                self.assertEqual(saved['files'][0]['path'], 'One.DLL')
                warning.assert_not_called()
            finally:
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
