"""Compile the real cached-extractor chain; never execute installers."""
import hashlib
from pathlib import Path
import subprocess

import pytest

from packager import inno
from packager.models import ResolvedSoftware
from test_inno import COMPILER, FIXTURE, compile_script


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_selected_dll_chain_compiles_and_rejects_changed_source_bytes(tmp_path):
    source = tmp_path / 'release {#literal}'
    (source / 'plugins {literal}').mkdir(parents=True)
    (source / 'app.exe').write_bytes(FIXTURE.read_bytes())
    dll = source / 'plugins {literal}' / 'a.dll'
    dll.write_bytes(b'original selected dependency')
    files = [{'path': 'plugins {literal}/a.dll', 'size': dll.stat().st_size,
              'sha256': hashlib.sha256(dll.read_bytes()).hexdigest()}]
    item = ResolvedSoftware('C', source, Path('app.exe'), 'Dependency Test', '1.0.0')
    language = COMPILER.parent / 'Languages' / 'ChineseSimplified.isl'
    app_id = '{A8758A09-AB2F-44A6-B0CB-F2F8C9BB4103}'
    extractor_text = inno.dependency_script(item, source, app_id, language, files)
    extractor = compile_script(tmp_path, extractor_text, 'dependencies')
    package = {'path': str(extractor), 'size': extractor.stat().st_size,
               'sha256': hashlib.sha256(extractor.read_bytes()).hexdigest()}
    child_text = inno.child_script(item, source, app_id, 'DependencyTest', language,
                                   ['app.exe', files[0]['path']], ['plugins {literal}'],
                                   dependency_package=package, dependency_files=files)
    # The compiler consumes all runtime external/Hash/space-check code here.
    child = compile_script(tmp_path, child_text, 'child')
    compile_script(tmp_path, inno.outer_script('Bundle', '1.0.0', [
        {'role': 'C', 'name': item.name, 'version': item.version, 'path': child}], [], language), 'outer')
    dll.write_bytes(b'modified selected dependency')
    changed = tmp_path / 'changed.iss'
    changed.write_text(extractor_text, encoding='utf-8-sig')
    failed = subprocess.run([str(COMPILER), '/O' + str(tmp_path), '/Fchanged', str(changed)],
                            capture_output=True, timeout=60)
    assert failed.returncode != 0
    assert not (tmp_path / 'changed.exe').exists()


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_child_rejects_dependency_package_replaced_after_validation(tmp_path):
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'app.exe').write_bytes(FIXTURE.read_bytes())
    helper = tmp_path / 'deps.exe'
    helper.write_bytes(FIXTURE.read_bytes())
    package = {'path': str(helper), 'size': helper.stat().st_size,
               'sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    item = ResolvedSoftware('C', source, Path('app.exe'), 'Test', '1.0')
    text = inno.child_script(item, source, '{0D144534-251C-4CA5-B344-7AE94B6D4360}', 'Test',
                             COMPILER.parent / 'Languages' / 'ChineseSimplified.isl', ['app.exe', 'a.dll'], [],
                             dependency_package=package,
                             dependency_files=[{'path': 'a.dll', 'size': 3,
                                                'sha256': hashlib.sha256(b'abc').hexdigest()}])
    helper.write_bytes(b'MZ replaced after validation')
    script = tmp_path / 'bad.iss'
    script.write_text(text, encoding='utf-8-sig')
    result = subprocess.run([str(COMPILER), '/O' + str(tmp_path), '/Fbad', str(script)],
                            capture_output=True, timeout=60)
    assert result.returncode != 0
    assert not (tmp_path / 'bad.exe').exists()
