"""Compiler resource paths are literal, including braces in installation paths."""
from pathlib import Path
import shutil
import subprocess

import pytest
from packager import inno
from packager.models import DEFAULT_COMPILER, ResolvedSoftware


@pytest.mark.skipif(not Path(DEFAULT_COMPILER).is_file(), reason='Inno compiler unavailable')
def test_child_and_outer_compile_language_file_under_literal_brace_directory(tmp_path):
    compiler = Path(DEFAULT_COMPILER)
    language = tmp_path / 'Inno {literal}' / 'ChineseSimplified.isl'
    language.parent.mkdir()
    shutil.copy2(compiler.parent / 'Languages' / language.name, language)
    release = tmp_path / 'release'
    release.mkdir()
    shutil.copy2(compiler.parent / 'Examples' / 'MyProg.exe', release / 'main.exe')
    software = ResolvedSoftware('C', release, Path('main.exe'), '测试软件', '1.0.0')
    child = inno.child_script(software, release, '{B93603E2-33A8-45E4-9AA7-01C0620FCF31}',
                              'LanguageTest-C', language, ['main.exe'], [])
    for name in ('child', 'outer'):
        script = tmp_path / (name + '.iss')
        script.write_text(child if name == 'child' else inno.outer_script('语言路径验证', '1.0.0',
                          [{'role': 'C', 'name': software.name, 'version': '1.0.0',
                            'path': tmp_path / 'child.exe'}], [], language), encoding='utf-8-sig')
        completed = subprocess.run([str(compiler), '/O' + str(tmp_path), '/F' + name, str(script)],
                                   capture_output=True, timeout=60)
        assert completed.returncode == 0, completed.stdout.decode('utf-8', 'replace') + completed.stderr.decode('utf-8', 'replace')
        assert (tmp_path / (name + '.exe')).read_bytes()[:2] == b'MZ'
