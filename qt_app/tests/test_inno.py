"""Compile artifacts and execute a payload-free directory validation harness."""
from pathlib import Path
import subprocess
import uuid
import configparser
import struct
import re
import ctypes
import sys

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


def test_outer_exposes_install_directory_page_and_passes_selected_dirs(tmp_path):
    child = tmp_path / 'c-child.exe'
    child.write_bytes(FIXTURE.read_bytes())
    script = inno.outer_script(
        'Bundle', '1.0.0',
        [{'role': 'C', 'path': child, 'name': 'Software C', 'version': '1.0.0',
          'app_id': '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}',
          'install_dir': 'Software-C', 'architecture': 'x64'}],
        [],
        COMPILER.parent / 'Languages' / 'ChineseSimplified.isl',
    )
    assert 'CreateInputDirPage' in script
    assert 'NextButtonClick' in script
    assert 'InstallDirPage.Values[0]' in script
    assert '/DIR="' in script


def test_child_exposes_an_explicit_uninstaller_entry(tmp_path):
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'app.exe').write_bytes(FIXTURE.read_bytes())
    item = ResolvedSoftware('C', source, Path('app.exe'), 'Software C', '1.0.0')
    script = inno.child_script(item, source, '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}', 'Software-C',
                               COMPILER.parent / 'Languages' / 'ChineseSimplified.isl', ['app.exe'], [], False)
    assert 'Uninstallable=yes' in script
    assert 'CreateUninstallRegKey=yes' in script
    assert 'UninstallDisplayIcon={app}\\app.exe' in script
    assert 'Filename: "{uninstallexe}"' in script


@pytest.mark.skipif(not COMPILER.exists(), reason='Real Inno compiler unavailable')
def test_install_directory_event_rejects_long_product_paths_before_changing_state(tmp_path):
    generated = inno.outer_script('Bundle', '1.0', [
        {'role': 'C', 'path': FIXTURE, 'name': 'C', 'version': '1.0'},
        {'role': 'A', 'path': FIXTURE, 'name': 'A', 'version': '1.0'},
    ], [], COMPILER.parent / 'Languages' / 'ChineseSimplified.isl')
    event = 'function NextButtonClick' + generated.split('function NextButtonClick', 1)[1]
    # Exercise the actual generated event in Inno. Remove only error dialogs
    # so rejected inputs can be checked unattended; no application is installed.
    event = re.sub(r'^\s*MsgBox\([^\n]*\);\n', '\n', event, flags=re.MULTILINE)
    root = 'C:\\' + 'x' * 232
    harness = f'''[Setup]
AppName=Directory event checks
AppVersion=1.0
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
Compression=none
[Types]
Name: "custom"; Description: "Custom"; Flags: iscustom
[Components]
Name: "C"; Description: "C"; Types: custom; Flags: fixed
Name: "A"; Description: "A"
[Code]
var
  InstallDirPage: TInputDirWizardPage;
  TaskDirs, TaskSubdirs, TaskRoles: TArrayOfString;
  SelectedInstallRoot, Checks: String;
{event}
procedure Check(Value: Boolean);
begin
  if Value then Checks := Checks + 'ok' + #13#10
  else Checks := Checks + 'failed' + #13#10;
end;
procedure InitializeWizard;
begin
  SetArrayLength(TaskDirs, 2);
  SetArrayLength(TaskSubdirs, 2);
  SetArrayLength(TaskRoles, 2);
  TaskRoles[0] := 'C'; TaskRoles[1] := 'A';
  TaskSubdirs[0] := 'Product-C'; TaskSubdirs[1] := 'Product-A';
  TaskDirs[0] := 'C:\\Original\\Product-C'; TaskDirs[1] := 'C:\\Original\\Product-A';
  SelectedInstallRoot := 'C:\\Original';
  InstallDirPage := CreateInputDirPage(wpSelectComponents, '', '', '', False, '');
  InstallDirPage.Add('Root');
  WizardSelectComponents('C');
  InstallDirPage.Values[0] := {inno.pascal(root)};
  Check(not NextButtonClick(InstallDirPage.ID));
  Check(TaskDirs[0] = 'C:\\Original\\Product-C');
  Check(SelectedInstallRoot = 'C:\\Original');
  TaskSubdirs[1] := StringOfChar('a', 240);
  InstallDirPage.Values[0] := 'D:\\Apps';
  Check(NextButtonClick(InstallDirPage.ID));
  Check(TaskDirs[0] = 'D:\\Apps\\Product-C');
  WizardSelectComponents('C,A');
  Check(not NextButtonClick(InstallDirPage.ID));
  InstallDirPage.Values[0] := 'E:\\Apps';
  Check(not NextButtonClick(InstallDirPage.ID));
  Check(TaskDirs[0] = 'D:\\Apps\\Product-C');
  TaskSubdirs[1] := 'Product-A';
  InstallDirPage.Values[0] := 'C:\\Original';
  Check(NextButtonClick(InstallDirPage.ID));
  Check(TaskDirs[0] = 'C:\\Original\\Product-C');
  Check(TaskDirs[1] = 'C:\\Original\\Product-A');
  SaveStringToFile({inno.pascal(tmp_path / 'checks.txt')}, Checks, False);
end;
'''
    executable = compile_script(tmp_path, harness, 'directory-checks')
    completed = subprocess.run([str(executable), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART'],
                               capture_output=True, timeout=30)
    assert completed.returncode == 0
    assert (tmp_path / 'checks.txt').read_text().splitlines() == ['ok'] * 11


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows icon-location parser required')
def test_uninstall_icon_preserves_commas_in_install_and_entry_paths(tmp_path):
    item = ResolvedSoftware('C', tmp_path, Path('bin/main,2.exe'), 'Product', '1.0')
    script = inno.child_script(item, tmp_path, '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}',
                               'Product-C', COMPILER.parent / 'Languages' / 'ChineseSimplified.isl',
                               ['bin/main,2.exe'], [], False)
    icon = next(line.split('=', 1)[1] for line in script.splitlines()
                if line.startswith('UninstallDisplayIcon='))
    icon = icon.replace('{app}', r'D:\Apps\ACME,2')
    parsed = ctypes.create_unicode_buffer(icon)
    index = ctypes.windll.shlwapi.PathParseIconLocationW(parsed)
    assert parsed.value == r'D:\Apps\ACME,2\bin\main,2.exe'
    assert index == 0


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
