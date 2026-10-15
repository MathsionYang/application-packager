"""Compile generated artifacts; never launch an installer on the build host."""
from pathlib import Path
import subprocess
import uuid
import configparser
import struct

import pytest

from packager import inno
from packager.models import ResolvedSoftware, RuntimeInstaller


COMPILER = Path(r'D:\PCAPP\Inno Setup 7\ISCC.exe')
FIXTURE = COMPILER.parent / 'Examples' / 'MyProg.exe'


def compile_script(tmp_path, text, name):
    script = tmp_path / f'{name}.iss'
    script.write_text(text, encoding='utf-8-sig')
    compiled = subprocess.run([str(COMPILER), f'/O{tmp_path}', f'/F{name}', str(script)],
                              capture_output=True, timeout=90)
    assert compiled.returncode == 0, compiled.stdout.decode('utf-8', 'replace') + compiled.stderr.decode('utf-8', 'replace')
    output = tmp_path / f'{name}.exe'
    assert output.read_bytes()[:2] == b'MZ'
    return output


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_child_compiles_nested_files_and_literal_braces_without_preprocessor_injection(tmp_path):
    source = tmp_path / '中文 发布 {#not_a_macro}'
    (source / 'nested {literal}').mkdir(parents=True)
    (source / 'empty').mkdir()
    (source / 'nested {literal}' / 'main.exe').write_bytes(FIXTURE.read_bytes())
    (source / 'setting.ini').write_text('user=default', encoding='utf-8')
    software = ResolvedSoftware('C', source, Path('nested {literal}/main.exe'), '测试 {#evil} "产品"', '1.0.0')
    script = inno.child_script(software, source, '{' + str(uuid.uuid4()) + '}', 'TestProduct',
                               COMPILER.parent / 'Languages' / 'ChineseSimplified.isl',
                               ['nested {literal}/main.exe', 'setting.ini'], ['empty', 'nested {literal}'], True)
    compile_script(tmp_path, script, 'child')


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
@pytest.mark.parametrize('roles', [('C',), ('C', 'A'), ('C', 'B'), ('C', 'A', 'B')])
def test_outer_compiles_every_component_combination_with_serial_runtime_plan(tmp_path, roles):
    children = []
    for role in roles:
        child = tmp_path / f'{role}-child.exe'
        child.write_bytes(FIXTURE.read_bytes())
        children.append({'role': role, 'path': child, 'name': f'软件 {role}', 'version': '1.0.0',
                         'app_id': '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}', 'install_dir': f'产品 {role} {{literal}}',
                         'architecture': 'x64'})
    runtime_exe = tmp_path / 'runtime {#evil}.exe'
    runtime_exe.write_bytes(FIXTURE.read_bytes())
    runtime_msi = tmp_path / 'same name.msi'
    runtime_msi.write_bytes(b'compile-only fixture, never executed')
    runtimes = [RuntimeInstaller(runtime_exe, '/quiet /norestart', (0, 1638, 3010)),
                RuntimeInstaller(runtime_msi, '/qn /norestart', (0, 3010))]
    script = inno.outer_script('套装 {#evil} "名称"', '1.2.3', children, runtimes,
                              COMPILER.parent / 'Languages' / 'ChineseSimplified.isl',
                              runtime_names=['原始同名环境包.exe', '原始同名环境包.msi'])
    compile_script(tmp_path, script, 'outer')


def test_outer_runtime_failure_is_optional_but_software_failure_still_aborts(tmp_path):
    """Runtime installers may be declined/failed without blocking child apps."""
    child = tmp_path / 'c-child.exe'
    child.write_bytes(FIXTURE.read_bytes())
    runtime = tmp_path / 'vc-redist.exe'
    runtime.write_bytes(FIXTURE.read_bytes())
    script = inno.outer_script(
        'Bundle', '1.0.0',
        [{'role': 'C', 'path': child, 'name': 'Software C', 'version': '1.0.0',
          'app_id': '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}',
          'install_dir': 'Software-C', 'architecture': 'x64'}],
        [RuntimeInstaller(runtime, '/quiet /norestart', (0, 3010))],
        COMPILER.parent / 'Languages' / 'ChineseSimplified.isl',
    )
    # The generated Pascal must handle optional runtime errors locally and
    # retain the existing fatal path for software child installers.
    assert 'Optional runtime skipped' in script
    assert "if TaskRoles[I] = '' then begin" in script
    assert "Failure := TaskNames[I]" in script


def test_identically_named_products_have_distinct_shortcut_groups_and_desktop_links(tmp_path):
    groups, icons = [], []
    for role in ('C', 'A', 'B'):
        item = ResolvedSoftware(role, tmp_path, Path('app.exe'), 'Same Product', '1.0.0')
        text = inno.child_script(item, tmp_path, '{' + str(uuid.uuid4()) + '}', 'Distinct-' + role,
                                 COMPILER.parent / 'Languages' / 'ChineseSimplified.isl', ['app.exe'], [], True)
        setup = configparser.ConfigParser(interpolation=None, strict=False)
        setup.read_string('[Setup]' + text.split('[Setup]', 1)[1].split('[Languages]', 1)[0])
        groups.append(setup['Setup']['DefaultGroupName'])
        icons.append(text.split('[Icons]', 1)[1].splitlines()[-1].split('; Filename:', 1)[0])
    assert len(set(groups)) == 3
    assert len(set(icons)) == 3


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
@pytest.mark.parametrize(('machine', 'allowed'), [(0x8664, 'x64compatible'), (0xAA64, 'arm64')])
def test_child_uses_snapshot_pe_machine_for_native_architecture_rules(tmp_path, machine, allowed):
    source = tmp_path / 'native'
    source.mkdir()
    payload = bytearray(FIXTURE.read_bytes())
    pe_offset = struct.unpack_from('<I', payload, 0x3C)[0]
    struct.pack_into('<H', payload, pe_offset + 4, machine)
    (source / 'app.exe').write_bytes(payload)
    item = ResolvedSoftware('C', source, Path('app.exe'), 'Architecture Fixture', '1.0.0')
    text = inno.child_script(item, source, '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}', 'ArchitectureFixture',
                             COMPILER.parent / 'Languages' / 'ChineseSimplified.isl', ['app.exe'], [], False)
    setup = configparser.ConfigParser(interpolation=None, strict=False)
    setup.read_string('[Setup]' + text.split('[Setup]', 1)[1].split('[Languages]', 1)[0])
    assert setup['Setup']['ArchitecturesAllowed'] == allowed
    assert setup['Setup']['ArchitecturesInstallIn64BitMode'] == allowed
    compile_script(tmp_path, text, 'architecture')
