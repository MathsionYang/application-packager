"""Automatic outer versions follow the selected C entry without running it."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from packager import builder
from packager.models import BundleConfig
from packager.store import AppStore
from packager.validation import ValidationError, validate_config
from test_builder import direct_build
from ui.mainwindow import MainWindow, ValidationThread


def test_legacy_config_defaults_to_auto_version_and_manual_choice_roundtrips():
    config = BundleConfig.from_dict({'version': '9.9.9'})
    assert config.auto_version is True
    config.auto_version = False
    restored = BundleConfig.from_dict(config.to_dict())
    assert restored.auto_version is False
    assert restored.version == '9.9.9'


@pytest.mark.parametrize('value', ['false', 0, None])
def test_config_rejects_nonboolean_auto_version(value):
    with pytest.raises(ValueError, match='版本'):
        BundleConfig.from_dict({'auto_version': value})


@pytest.mark.parametrize('old_version', ['', 'stale invalid version'])
def test_auto_version_does_not_validate_obsolete_manual_value(direct_build, old_version):
    config, _, _ = direct_build
    config.version = old_version
    assert validate_config(config, check_files=False)[0].role == 'C'
    config.auto_version = False
    with pytest.raises(ValidationError, match='版本'):
        validate_config(config, check_files=False)


@pytest.mark.parametrize(('fields', 'expected'), [
    ({'ProductVersion': '5.6.7', 'FileVersion': '9.8.7.6'}, '5.6.7'),
    ({'ProductVersion': '5, 6, 7, 8'}, '5.6.7.8'),
    ({'ProductVersion': '5.6.7-beta'}, '5.6.7'),
    ({'ProductVersion': '', 'FileVersion': '4.3.2.1'}, '4.3.2.1'),
    ({'ProductVersion': 'invalid', 'FileVersion': '4.3.2.1'}, '4.3.2.1'),
    ({'ProductVersion': '1.2.3.4.5', 'FileVersion': '4.3.2.1'}, '4.3.2.1'),
    ({'FileVersion': 'invalid'}, '1.0.0'),
    ({}, '1.0.0'),
])
def test_auto_bundle_version_controls_output_script_and_saved_config(direct_build, tmp_path, monkeypatch, fields, expected):
    config, sources, _ = direct_build
    config.version = 'stale invalid version'
    def metadata(path):
        role = next(role for role, source in sources.items() if path.parent == source)
        return fields if role == 'C' else {'ProductVersion': '2.1.0' if role == 'A' else '3.2.0'}
    monkeypatch.setattr(builder, 'read_product_metadata', metadata)
    messages = []
    result = builder.build_bundle(config, profile_path=tmp_path / 'products.json', log=messages.append)
    manifest = json.loads(result.manifest_file.read_text(encoding='utf-8'))
    saved = json.loads((result.artifact_dir / 'config.json').read_text(encoding='utf-8'))
    assert saved['version'] == expected
    assert saved['auto_version'] is True
    assert manifest['config']['version'] == expected
    assert f'-{expected}-' in result.output_file.name
    outer = (result.artifact_dir / 'scripts' / 'bundle.iss').read_text(encoding='utf-8-sig')
    assert f'AppVersion={expected}\n' in outer
    assert {item['role']: item['version'] for item in manifest['software']} == {
        'C': expected, 'A': '2.1.0', 'B': '3.2.0'}
    assert config.version == 'stale invalid version', 'caller-owned config must remain isolated'
    assert any('软件 C' in message and expected in message for message in messages)
    if expected == '1.0.0':
        assert any('缺省' in message and '1.0.0' in message for message in messages)


def test_manual_bundle_version_keeps_per_software_exe_versions(direct_build, tmp_path, monkeypatch):
    config, _, _ = direct_build
    config.auto_version = False
    config.version = '9.8.7'
    monkeypatch.setattr(builder, 'read_product_metadata', lambda path: {'ProductVersion': '3.2.1'})
    result = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    manifest = json.loads(result.manifest_file.read_text(encoding='utf-8'))
    assert manifest['config']['version'] == '9.8.7'
    assert {item['version'] for item in manifest['software']} == {'3.2.1'}
    assert '-9.8.7-' in result.output_file.name


def test_automatic_version_update_retains_app_id_and_records_new_source_metadata(direct_build, tmp_path, monkeypatch):
    config, _, _ = direct_build
    fields = {'ProductName': 'Stable product', 'ProductVersion': '2.0.0'}
    monkeypatch.setattr(builder, 'read_product_metadata', lambda path: dict(fields))
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    first_manifest = json.loads(first.manifest_file.read_text(encoding='utf-8'))
    fields['ProductVersion'] = '2.0.1'
    second = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    second_manifest = json.loads(second.manifest_file.read_text(encoding='utf-8'))
    assert first_manifest['config']['version'] == '2.0.0'
    assert second_manifest['config']['version'] == '2.0.1'
    assert [item['app_id'] for item in first_manifest['software']] == [item['app_id'] for item in second_manifest['software']]
    assert second_manifest['software'][0]['metadata']['ProductVersion'] == '2.0.1'


def test_preflight_reads_only_c_entry_without_scanning_or_hashing_tree(direct_build, monkeypatch):
    config, sources, _ = direct_build
    def forbidden(*args, **kwargs):
        raise AssertionError('version preview must not scan or hash software trees')
    monkeypatch.setattr(builder, '_scan', forbidden)
    monkeypatch.setattr(builder, '_hash', forbidden)
    def metadata(path):
        assert path == sources['C'] / 'app.exe'
        return {'ProductVersion': '7.8.9'}
    monkeypatch.setattr(builder, 'read_product_metadata', metadata)
    worker = ValidationThread(config, threading.Event(), None)
    outcomes = []
    worker.outcome.connect(outcomes.append)
    worker.run()
    assert outcomes[0][0] == 'valid'
    assert outcomes[0][1].version == '7.8.9'


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    monkeypatch.setattr('ui.mainwindow.QMessageBox.warning', lambda *args: None)
    value = MainWindow(AppStore(tmp_path / 'state'))
    yield value
    if value.building:
        value.cancel_event.set()
        deadline = time.monotonic() + 5
        while value.building and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
    value.close()
    value.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_ui_auto_version_defaults_readonly_and_manual_choice_restores_editing(window):
    assert window.auto_version_check.isChecked()
    assert window.version_input.isReadOnly()
    window.auto_version_check.setChecked(False)
    assert not window.version_input.isReadOnly()
    window.version_input.setText('4.5.6')
    assert window.current_config().auto_version is False
    assert window.store.load_config().version == '4.5.6'
    window.apply_config(BundleConfig.from_dict(window.current_config().to_dict()))
    assert not window.auto_version_check.isChecked()
    assert not window.version_input.isReadOnly()


@pytest.mark.parametrize('fail_compile', [False, True])
def test_gui_authoritative_builder_version_updates_display_config_and_history(direct_build, window, monkeypatch, fail_compile):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    config.version = ''
    reads = []
    def metadata(path):
        reads.append(path)
        return {'ProductVersion': '2.0.0' if len(reads) == 1 else '3.0.0'}
    monkeypatch.setattr(builder, 'read_product_metadata', metadata)
    if fail_compile:
        def failing_compile(*args):
            raise RuntimeError('deliberate compile failure after version resolution')
        monkeypatch.setattr(builder, '_compile', failing_compile)
    monkeypatch.setattr(window, 'show_record', lambda record: None)
    window.apply_config(config)
    window.start_build()
    deadline = time.monotonic() + 5
    while window.building and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not window.building, 'background fixture build exceeded five seconds'
    history = window.store.load_history()
    assert history[0]['status'] == ('failed' if fail_compile else 'success'), window.build_logs
    assert window.version_input.text() == '3.0.0'
    assert window.active_config.version == '3.0.0'
    assert window.store.load_config().version == '3.0.0'
    assert history[0]['config']['version'] == '3.0.0'
    if not fail_compile:
        assert '-3.0.0-' in history[0]['name']


def test_selected_ambiguous_c_entry_sets_automatic_bundle_version(direct_build, window, monkeypatch):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    (sources['C'] / 'helper.exe').write_bytes(b'MZ helper')
    monkeypatch.setattr('ui.mainwindow.QInputDialog.getItem', lambda *args: ('app.exe', True))
    monkeypatch.setattr(builder, 'read_product_metadata', lambda path: {
        'ProductVersion': '6.5.4' if path.name == 'app.exe' else '9.9.9'})
    monkeypatch.setattr(window, 'show_record', lambda record: None)
    window.apply_config(config)
    window.start_build()
    deadline = time.monotonic() + 5
    while window.building and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not window.building
    history = window.store.load_history()
    assert history[0]['status'] == 'success', window.build_logs
    assert history[0]['config']['entries']['C'] == 'app.exe'
    assert history[0]['config']['version'] == '6.5.4'
