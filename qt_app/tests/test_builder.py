import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from packager import builder
from packager import inno
from packager.models import BundleConfig, ResolvedSoftware, RuntimeInstaller


COMPILER = Path(r'D:\PCAPP\Inno Setup 7\ISCC.exe')
FIXTURE = COMPILER.parent / 'Examples' / 'MyProg.exe'


def release(tmp_path, name='发布 C'):
    source = tmp_path / name
    (source / 'plugins').mkdir(parents=True)
    (source / 'empty').mkdir()
    (source / 'app.exe').write_bytes(FIXTURE.read_bytes())
    (source / 'plugins' / 'library.dll').write_bytes(b'full dependency content')
    (source / '.hidden.ini').write_bytes(b'user-setting=1')
    return source


def test_profiles_survive_relocation_version_and_bundle_changes_but_separate_products(tmp_path):
    profile = tmp_path / 'products.json'
    old = ResolvedSoftware('C', Path('D:/old'), Path('bin/main.exe'), 'Product', '1.0.0')
    new = ResolvedSoftware('C', Path('E:/new'), Path('bin/main.exe'), 'Product', '9.0.0')
    first = builder._product_profiles([old], profile)
    second = builder._product_profiles([new, ResolvedSoftware('A', Path('F:/other'), Path('main.exe'), 'Product', '2.0.0')], profile)
    assert first['C']['app_id'] == second['C']['app_id']
    assert first['C']['install_dir'] == second['C']['install_dir']
    assert second['C']['app_id'] != second['A']['app_id']
    different = builder._product_profiles([ResolvedSoftware('C', Path('E:/new'), Path('replacement.exe'), 'Other product', '1.0.0')], profile)
    assert different['C']['app_id'] != first['C']['app_id']


def test_corrupt_product_profile_is_preserved_and_blocks_accidental_new_identity(tmp_path):
    path = tmp_path / 'products.json'
    path.write_bytes(b'not json')
    item = ResolvedSoftware('C', tmp_path, Path('app.exe'), 'Product', '1.0.0')
    with pytest.raises(ValueError, match='产品'):
        builder._product_profiles([item], path)
    assert path.read_bytes() == b'not json'


@pytest.fixture
def direct_build(tmp_path, monkeypatch):
    """Exercise the real build pipeline without launching the external compiler."""
    sources = {}
    for role in ('C', 'A', 'B'):
        source = tmp_path / ('发布 ' + role)
        (source / 'plugins').mkdir(parents=True)
        (source / 'empty').mkdir()
        (source / 'app.exe').write_bytes(b'MZ compile-only entry')
        (source / 'plugins' / 'library.dll').write_bytes(b'dependency')
        (source / '.hidden.ini').write_bytes(b'user=default')
        sources[role] = source
    compiler = tmp_path / 'compiler' / 'ISCC.exe'
    compiler.parent.mkdir()
    compiler.write_bytes(b'compile-only fixture')
    (compiler.parent / 'Languages').mkdir()
    (compiler.parent / 'Languages' / 'ChineseSimplified.isl').write_text('fixture')
    config = BundleConfig(paths={role: str(path) for role, path in sources.items()},
                          output=str(tmp_path / 'output'), compiler=str(compiler))
    def compile_fixture(compiler, script, output_dir, output_name, log, cancel):
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / (output_name + '.exe')
        target.write_bytes(b'MZ compile-only installer ' + output_name.encode('ascii'))
        return target
    monkeypatch.setattr(builder, '_compile', compile_fixture)
    monkeypatch.setattr(builder, 'read_product_metadata', lambda path: {})
    return config, sources, compile_fixture


def test_direct_source_build_keeps_full_tree_without_copying_or_hashing_inputs(direct_build, tmp_path, monkeypatch):
    config, sources, _ = direct_build
    expected = {role: {path.relative_to(source).as_posix(): path.read_bytes()
                       for path in source.rglob('*') if path.is_file()}
                for role, source in sources.items()}
    original_hash, original_copy = builder._hash, builder._copy_file
    def is_software(path):
        return any(Path(path).resolve().is_relative_to(source) for source in sources.values())
    def input_hash_forbidden(path, cancel=None):
        assert not is_software(path), 'Software source files must not be read for full hashes'
        return original_hash(path, cancel)
    def input_copy_forbidden(source, target, cancel):
        assert not is_software(source), 'Software source files must not be copied'
        return original_copy(source, target, cancel)
    monkeypatch.setattr(builder, '_hash', input_hash_forbidden)
    monkeypatch.setattr(builder, '_copy_file', input_copy_forbidden)
    result = builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    manifest = json.loads(result.manifest_file.read_text(encoding='utf-8'))
    assert manifest['schema_version'] == 2
    assert manifest['input_mode'] == 'direct_source'
    assert manifest['source_integrity'] == 'metadata_before_after'
    for item in manifest['software']:
        source = sources[item['role']]
        assert {file['path'] for file in item['files']} == {'app.exe', '.hidden.ini', 'plugins/library.dll'}
        assert 'empty' in item['directories']
        for file in item['files']:
            original = source / file['path']
            assert file['size'] == original.stat().st_size
            assert file['mtime_ns'] == original.stat().st_mtime_ns
            assert 'sha256' not in file
        script = (result.artifact_dir / 'scripts' / ('child-' + item['role'] + '.iss')).read_text(encoding='utf-8-sig')
        assert f'Source: "{source / "app.exe"}"' in script
        assert item['installer']['sha256']
        assert {path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob('*') if path.is_file()} == expected[item['role']]
    assert manifest['output']['sha256'] == hashlib.sha256(result.output_file.read_bytes()).hexdigest()
    assert all(item['sha256'] == hashlib.sha256((result.artifact_dir / item['path']).read_bytes()).hexdigest()
               for item in manifest['artifacts'])
    assert not list(Path(config.output).glob('.build-*'))


@pytest.mark.parametrize('change', ['add', 'delete', 'modify'])
def test_direct_source_changes_during_compile_fail_without_publishing_or_deleting_inputs(direct_build, tmp_path, monkeypatch, change):
    config, sources, compile_fixture = direct_build
    source = sources['C']
    output = Path(config.output)
    output.mkdir()
    old = output / 'old-Bundle.exe'
    old.write_bytes(b'old artifact must survive')
    original_entry = (source / 'app.exe').read_bytes()
    def changing_compile(compiler, script, output_dir, output_name, log, cancel):
        result = compile_fixture(compiler, script, output_dir, output_name, log, cancel)
        if output_name == 'child-C':
            dependency = source / 'plugins' / 'library.dll'
            if change == 'add':
                (source / 'late.dat').write_bytes(b'added during compilation')
            elif change == 'delete':
                dependency.unlink()
            else:
                previous = dependency.stat()
                dependency.write_bytes(b'newcontent')  # same size: mtime must also be checked
                os.utime(dependency, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10_000_000))
        return result
    monkeypatch.setattr(builder, '_compile', changing_compile)
    with pytest.raises(RuntimeError, match='变化|变更'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert list(output.glob('*.exe')) == [old]
    assert old.read_bytes() == b'old artifact must survive'
    assert source.is_dir()
    assert (source / 'app.exe').read_bytes() == original_entry
    assert all(path.is_dir() for path in sources.values())
    manifest_path, = output.glob('build-*/manifest.json')
    assert json.loads(manifest_path.read_text(encoding='utf-8'))['status'] == 'failed'
    assert not list(output.glob('.build-*'))


def test_direct_source_entry_change_after_metadata_read_is_rejected(direct_build, tmp_path, monkeypatch):
    config, sources, _ = direct_build
    original_profiles = builder._product_profiles
    def profiles_then_change_entry(software, path):
        profiles = original_profiles(software, path)
        (sources['C'] / 'app.exe').write_bytes(b'MZ changed after metadata was read')
        return profiles
    monkeypatch.setattr(builder, '_product_profiles', profiles_then_change_entry)
    with pytest.raises(RuntimeError, match='变化|变更'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert not list(Path(config.output).glob('*.exe'))
    assert (sources['C'] / 'app.exe').is_file()


@pytest.mark.parametrize('target_exists', [False, True])
def test_direct_source_scan_rejects_broken_and_existing_symlinks(tmp_path, target_exists):
    source = tmp_path / 'release'
    source.mkdir()
    target = tmp_path / 'outside.dat'
    if target_exists:
        target.write_bytes(b'outside source')
    try:
        (source / 'linked.dat').symlink_to(target)
    except OSError as error:
        pytest.skip(f'Creating symlinks is unavailable: {error}')
    with pytest.raises(ValueError, match='链接|重解析|越出'):
        builder._scan(source, threading.Event())


def test_process_streams_large_output_without_pipe_deadlock_and_surfaces_exit_status():
    lines = []
    status = builder._run_process([sys.executable, '-u', '-c', "import sys; [print('x'*4096) for _ in range(400)]; sys.exit(7)"], lines.append, threading.Event())
    assert status == 7
    assert len(lines) == 400
    assert sum(len(line) for line in lines) >= 1_600_000


def test_cancellation_terminates_active_process_promptly():
    cancel = threading.Event()
    started = time.monotonic()
    def log(line):
        if line == 'started':
            cancel.set()
    with pytest.raises(builder.BuildCancelled):
        builder._run_process([sys.executable, '-u', '-c', "import time; print('started',flush=True); time.sleep(60)"], log, cancel)
    assert time.monotonic() - started < 5


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_real_bundle_contains_all_sources_unique_runtimes_and_reviewable_artifacts(tmp_path):
    sources = {role: release(tmp_path, f'发布 {role} {{#literal}}') for role in ('C', 'A', 'B')}
    runtime1 = tmp_path / 'output' / 'runtime 1' / 'same.exe'
    runtime2 = tmp_path / 'output' / 'runtime 2' / 'same.exe'
    for path in (runtime1, runtime2):
        path.parent.mkdir(parents=True)
        path.write_bytes(FIXTURE.read_bytes())
    config = BundleConfig(paths={role: str(path) for role, path in sources.items()}, output=str(tmp_path / 'output'),
                          name='测试 套装 {#literal}', version='2.3.4',
                          runtimes=[RuntimeInstaller(runtime1), RuntimeInstaller(runtime2)], compiler=str(COMPILER))
    logs, progress = [], []
    result = builder.build_bundle(config, profile_path=tmp_path / 'products.json', log=logs.append,
                                  progress=lambda percent, stage: progress.append((percent, stage)))
    manifest = json.loads(result.manifest_file.read_text(encoding='utf-8'))
    assert result.output_file.read_bytes()[:2] == b'MZ'
    assert manifest['status'] == 'success'
    assert manifest['output']['sha256'] == hashlib.sha256(result.output_file.read_bytes()).hexdigest()
    artifact_names = {item['path'] for item in manifest['artifacts']}
    assert {'config.json', 'products.json', 'scripts/bundle.iss', 'logs/bundle.log', 'logs/build.log'} <= artifact_names
    assert all(item['sha256'] == hashlib.sha256((result.artifact_dir / item['path']).read_bytes()).hexdigest()
               for item in manifest['artifacts'])
    assert runtime1.read_bytes() == FIXTURE.read_bytes()
    assert runtime2.read_bytes() == FIXTURE.read_bytes()
    assert set(item['role'] for item in manifest['software']) == {'C', 'A', 'B'}
    assert len(set(item['app_id'] for item in manifest['software'])) == 3
    assert len(set(item['bundled_name'] for item in manifest['runtimes'])) == 2
    for item in manifest['software']:
        assert {file['path'] for file in item['files']} == {'.hidden.ini', 'app.exe', 'plugins/library.dll'}
        assert 'empty' in item['directories']
        assert item['version'] == '1.0.0'  # no PE metadata; not the outer bundle version
    assert (result.artifact_dir / 'products.json').is_file()
    assert len(list((result.artifact_dir / 'scripts').glob('*.iss'))) == 4
    assert len(list((result.artifact_dir / 'logs').glob('*.log'))) >= 4
    assert progress[-1][0] == 100
    assert any('Inno Setup' in line for line in logs)
    assert not list(Path(config.output).glob('.build-*'))
    assert all((path / 'plugins' / 'library.dll').read_bytes() == b'full dependency content' for path in sources.values())


def test_compiler_failure_never_reports_stale_success_and_keeps_failure_evidence(tmp_path, monkeypatch):
    source = release(tmp_path)
    output = tmp_path / 'output'
    output.mkdir()
    stale = output / 'old-Bundle.exe'
    stale.write_bytes(b'old artifact must survive')
    def failing_compile(*args, **kwargs):
        raise RuntimeError('deliberate compiler failure')
    monkeypatch.setattr(builder, '_compile', failing_compile)
    config = BundleConfig(paths={'C': str(source)}, output=str(output), compiler=str(COMPILER))
    with pytest.raises(RuntimeError, match='deliberate compiler failure'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert stale.read_bytes() == b'old artifact must survive'
    assert list(output.glob('*.exe')) == [stale]
    manifest_paths = list(output.glob('build-*/manifest.json'))
    assert len(manifest_paths) == 1
    assert json.loads(manifest_paths[0].read_text(encoding='utf-8'))['status'] == 'failed'
    assert not list(output.glob('.build-*'))


def test_cancel_before_build_has_no_output_or_source_side_effects(tmp_path):
    source = release(tmp_path)
    cancel = threading.Event()
    cancel.set()
    config = BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), compiler=str(COMPILER))
    with pytest.raises(builder.BuildCancelled):
        builder.build_bundle(config, cancel=cancel, profile_path=tmp_path / 'products.json')
    assert not Path(config.output).exists()
    assert not (tmp_path / 'products.json').exists()


def test_product_profile_inside_source_is_rejected_without_writing_inputs(tmp_path):
    source = release(tmp_path)
    config = BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), compiler=str(COMPILER))
    with pytest.raises(ValueError, match='产品|发布'):
        builder.build_bundle(config, profile_path=source / 'products.json')
    assert not (source / 'products.json').exists()


def test_source_entry_change_during_metadata_read_is_detected_before_build(tmp_path, monkeypatch):
    source = release(tmp_path)
    def changed_metadata(path):
        path.write_bytes(path.read_bytes() + b'changed during metadata read')
        return {}
    monkeypatch.setattr(builder, 'read_product_metadata', changed_metadata)
    config = BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), compiler=str(COMPILER))
    with pytest.raises(RuntimeError, match='变化|变更'):
        builder.build_bundle(config, profile_path=tmp_path / 'products.json')
    assert not Path(config.output).exists()


def test_compiler_zero_exit_without_current_output_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, '_run_process', lambda *args: 0)
    script = tmp_path / 'absent-output.iss'
    script.write_text('[Setup]\nAppName=FailureFixture', encoding='utf-8')
    with pytest.raises(RuntimeError, match='未生成'):
        builder._compile(COMPILER, script, tmp_path / 'compiled', 'current', lambda _: None, threading.Event())


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_cancel_during_real_compiler_stops_and_preserves_cancelled_artifacts(tmp_path):
    source = release(tmp_path)
    cancel = threading.Event()
    progress = []
    def log(line):
        if line.startswith('Inno Setup 7'):
            cancel.set()
    config = BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), compiler=str(COMPILER))
    with pytest.raises(builder.BuildCancelled):
        builder.build_bundle(config, cancel=cancel, log=log, profile_path=tmp_path / 'products.json',
                              progress=lambda value, stage: progress.append(value))
    output = Path(config.output)
    assert not list(output.glob('*.exe'))
    assert not list(output.glob('.build-*'))
    manifest_path, = output.glob('build-*/manifest.json')
    assert json.loads(manifest_path.read_text(encoding='utf-8'))['status'] == 'cancelled'
    assert 'Inno Setup' in (manifest_path.parent / 'logs' / 'child-C.log').read_text(encoding='utf-8')
    assert max(progress) < 100


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_pe_metadata_roundtrip_reads_unicode_name_and_product_version_without_execution(tmp_path):
    source = release(tmp_path)
    item = ResolvedSoftware('C', source, Path('app.exe'), '元数据产品 {#literal}', '5.6.7')
    text = inno.child_script(item, source, '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}', 'MetadataTest',
                             COMPILER.parent / 'Languages' / 'ChineseSimplified.isl', ['app.exe'], [], False)
    script = tmp_path / 'metadata.iss'
    script.write_text(text, encoding='utf-8-sig')
    executable = builder._compile(COMPILER, script, tmp_path, 'metadata', lambda _: None, threading.Event())
    fields = builder.read_product_metadata(executable)
    assert fields['ProductName'] == '元数据产品 {#literal}'
    assert fields['ProductVersion'].startswith('5.6.7')
