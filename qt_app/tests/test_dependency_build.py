import json
import os
from pathlib import Path
import threading

import pytest

from packager import builder
from packager.models import BundleConfig, DEFAULT_COMPILER
from test_builder import direct_build


def test_build_reuses_unchanged_dependency_then_rebuilds_same_metadata_content_change(direct_build, tmp_path, monkeypatch):
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    compiled = []
    def compile_count(*args):
        compiled.append(args[3])
        return original_compile(*args)
    monkeypatch.setattr(builder, '_compile', compile_count)
    profile = tmp_path / 'products.json'
    first = builder.build_bundle(config, profile_path=profile)
    assert 'dependencies-C' in compiled
    assert first.dependency_records['C']
    record = json.loads(Path(first.dependency_records['C']).read_text(encoding='utf-8'))
    assert record['kind'] == 'dependency_package'
    assert record['files'][0]['path'] == 'plugins/library.dll'
    compiled.clear()
    config.dependency_records = dict(first.dependency_records)
    second = builder.build_bundle(config, profile_path=profile)
    assert compiled == ['child-C', 'bundle']
    manifest = json.loads(second.manifest_file.read_text(encoding='utf-8'))
    assert manifest['software'][0]['dependency']['reused'] is True
    dll = sources['C'] / 'plugins/library.dll'
    before = dll.stat()
    dll.write_bytes(b'DEPENDENCY')  # deliberately preserve both size and mtime
    os.utime(dll, ns=(before.st_atime_ns, before.st_mtime_ns))
    compiled.clear()
    third = builder.build_bundle(config, profile_path=profile)
    assert 'dependencies-C' in compiled
    assert json.loads(third.manifest_file.read_text(encoding='utf-8'))['software'][0]['dependency']['reused'] is False
    # Old successful records remain immutable.
    assert json.loads(Path(first.dependency_records['C']).read_text(encoding='utf-8')) == record


def test_missing_selected_dll_blocks_build_without_using_old_package(direct_build, tmp_path):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    config.dependency_records = dict(first.dependency_records)
    (sources['C'] / 'plugins/library.dll').unlink()
    with pytest.raises((ValueError, FileNotFoundError), match='缺失|不存在|找不到'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert first.output_file.exists()
    assert len(list(Path(config.output).glob('*-Bundle.exe'))) == 1


def test_dependency_compile_failure_never_publishes_new_baseline(direct_build, tmp_path, monkeypatch):
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    previous = Path(first.dependency_records['C']).read_bytes()
    (sources['C'] / 'plugins/library.dll').write_bytes(b'new dependency')
    def fail_dependency(*args):
        if args[3] == 'dependencies-C':
            raise RuntimeError('fixture dependency compilation failure')
        return original_compile(*args)
    monkeypatch.setattr(builder, '_compile', fail_dependency)
    with pytest.raises(RuntimeError, match='fixture'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert Path(first.dependency_records['C']).read_bytes() == previous
    assert len(list(Path(config.output).glob('*-Bundle.exe'))) == 1


def test_changing_unselected_file_keeps_dependency_cache_but_corruption_rebuilds(direct_build, tmp_path, monkeypatch):
    from packager.dependencies import load_record
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    record = load_record(Path(first.dependency_records['C']))
    (sources['C'] / 'new-unselected.dll').write_bytes(b'main package content')
    compiled = []
    def compile_count(*args):
        compiled.append(args[3])
        return original_compile(*args)
    monkeypatch.setattr(builder, '_compile', compile_count)
    builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert compiled == ['child-C', 'bundle']
    Path(record['package']['path']).write_bytes(b'MZ corrupted cached package')
    compiled.clear()
    builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert 'dependencies-C' in compiled


def test_main_compile_failure_does_not_activate_new_dependency_cache(direct_build, tmp_path, monkeypatch):
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    def fail_main(*args):
        if args[3] == 'child-C':
            raise RuntimeError('fixture main compilation failure')
        return original_compile(*args)
    monkeypatch.setattr(builder, '_compile', fail_main)
    with pytest.raises(RuntimeError, match='fixture'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert not list((tmp_path / 'cache').rglob('latest.json'))
    assert not list((tmp_path / 'cache').rglob('*.exe'))


def test_clearing_dll_selection_restores_full_main_package(direct_build, tmp_path):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    config.dependency_records = dict(first.dependency_records)
    config.dependency_files = {'C': []}
    second = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert second.dependency_records == {}
    manifest = json.loads(second.manifest_file.read_text(encoding='utf-8'))
    assert 'dependency' not in manifest['software'][0]


def test_late_cancel_keeps_new_cache_generation_inactive(direct_build, tmp_path):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    cancel = threading.Event()
    def cancel_on_publication(percent, message):
        if percent == 98:
            cancel.set()
    with pytest.raises(builder.BuildCancelled):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json',
                             cancel=cancel, progress=cancel_on_publication)
    assert not list((tmp_path / 'cache').rglob('latest.json'))
    assert not list(Path(config.output).glob('*-Bundle.exe'))


def test_compiler_dll_change_invalidates_dependency_cache(direct_build, tmp_path, monkeypatch):
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    compiler_component = Path(config.compiler).parent / 'ISCmplr.dll'
    compiler_component.write_bytes(b'fixture compiler first version')
    builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    compiler_component.write_bytes(b'fixture compiler next version')
    compiled = []
    def compile_count(*args):
        compiled.append(args[3])
        return original_compile(*args)
    monkeypatch.setattr(builder, '_compile', compile_count)
    builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert 'dependencies-C' in compiled


def test_case_only_dll_rename_restores_selection_using_actual_relative_path(direct_build, tmp_path):
    config, sources, _ = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    (sources['C'] / 'plugins/library.dll').rename(sources['C'] / 'plugins/LIBRARY.DLL')
    result = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    record = json.loads(Path(result.dependency_records['C']).read_text(encoding='utf-8'))
    assert record['files'][0]['path'] == 'plugins/LIBRARY.DLL'


def test_changed_temporary_helper_cannot_be_published_under_old_dll_key(direct_build, tmp_path, monkeypatch):
    config, sources, original_compile = direct_build
    config.paths = {'C': str(sources['C'])}
    config.dependency_files = {'C': ['plugins/library.dll']}
    helper = None
    def change_after_main_compile(*args):
        nonlocal helper
        result = original_compile(*args)
        if args[3] == 'dependencies-C':
            helper = result
        elif args[3] == 'bundle':
            helper.write_bytes(b'MZ replaced temporary dependency installer')
        return result
    monkeypatch.setattr(builder, '_compile', change_after_main_compile)
    with pytest.raises(RuntimeError, match='依赖包.*变化'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert not list(Path(config.output).glob('*-Bundle.exe'))
    assert not list((tmp_path / 'cache').rglob('latest.json'))


@pytest.mark.skipif(not Path(DEFAULT_COMPILER).is_file(), reason='Real Inno compiler unavailable')
def test_real_compiler_builds_then_reuses_complete_dependency_chain(tmp_path):
    from packager.dependencies import load_record
    compiler = Path(DEFAULT_COMPILER)
    source = tmp_path / 'release'
    (source / 'lib').mkdir(parents=True)
    (source / 'app.exe').write_bytes((compiler.parent / 'Examples' / 'MyProg.exe').read_bytes())
    (source / 'lib' / 'dependency.dll').write_bytes(b'selected fixture DLL' * 1024)
    (source / 'lib' / 'module.pyd').write_bytes(b'unselected fixture module' * 128)
    config = BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'),
                          dependency_files={'C': ['lib/dependency.dll']})
    first = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    config.dependency_records = dict(first.dependency_records)
    second = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    a = json.loads(first.manifest_file.read_text(encoding='utf-8'))
    b = json.loads(second.manifest_file.read_text(encoding='utf-8'))
    assert a['software'][0]['dependency']['reused'] is False
    assert b['software'][0]['dependency']['reused'] is True
    assert load_record(Path(first.dependency_records['C']))['package']['sha256'] == \
        load_record(Path(second.dependency_records['C']))['package']['sha256']
    assert first.output_file.read_bytes()[:2] == second.output_file.read_bytes()[:2] == b'MZ'
