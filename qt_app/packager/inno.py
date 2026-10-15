"""Inno Setup 7 scripts. All interpolated values are data, never directives/code."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
import os
import re
import struct
import uuid

from .models import ROLE_NAMES


def _single_line(value: object) -> str:
    text = str(value)
    if any(ord(char) < 32 for char in text):
        raise ValueError('Inno values must not contain control characters')
    return text


def quoted(value: object, *, constants: bool = False) -> str:
    """A section parameter; braces in user data must never become constants."""
    text = _single_line(value)
    if not constants:
        text = text.replace('{', '{{')
    return '"' + text.replace('"', '""') + '"'


def pascal(value: object) -> str:
    return "'" + _single_line(value).replace("'", "''") + "'"


def directive(value: object) -> str:
    # [Setup] values are NOT quoted section parameters. Quoting here would
    # become part of the product identity/name and break registry lookups.
    return _single_line(value).replace('{', '{{')


def safe_filename(value: object) -> str:
    value = re.sub(r'[<>:"/\\|?*]', '_', _single_line(value)).rstrip('. ').strip()[:100]
    if not value or value.split('.')[0].casefold() in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
        return 'Product_' + value
    return value


def _win(value: object) -> str:
    return str(PureWindowsPath(value))


def read_pe_architecture(path: Path) -> str:
    """Read the entry's COFF machine; never infer architecture from this host."""
    try:
        with path.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                return 'unknown'
            offset = struct.unpack_from('<I', header, 0x3C)[0]
            if offset < 64 or offset > path.stat().st_size - 24:
                return 'unknown'
            stream.seek(offset)
            coff = stream.read(24)
            if coff[:4] != b'PE\0\0':
                return 'unknown'
            machine = struct.unpack_from('<H', coff, 4)[0]
            return {0x014C: 'x86', 0x8664: 'x64', 0xAA64: 'arm64', 0xA641: 'arm64'}.get(machine, 'unknown')
    except (OSError, ValueError, struct.error):
        return 'unknown'


def _script(text: str) -> str:
    # ISPP scans {#...} even inside quoted strings. Move its inline delimiter
    # to a freshly chosen token absent from ALL input, including names/paths.
    token = 'PACKAGER_INLINE_' + uuid.uuid4().hex
    while token in text:
        token = 'PACKAGER_INLINE_' + uuid.uuid4().hex
    return f'#pragma inlinestart "{token}"\n' + text


def is_mutable(relative: str) -> bool:
    """Conservative first-release upgrade policy for recognizable user data."""
    path = PureWindowsPath(relative)
    parts = {part.casefold() for part in path.parts[:-1]}
    return bool(parts & {'config', 'configs', 'configuration', 'settings', 'userdata', 'user-data', 'logs'}) or (
        path.suffix.casefold() in {'.ini', '.cfg', '.conf', '.config', '.db', '.sqlite', '.sqlite3'}
        or path.name.casefold() in {'appsettings.json', 'settings.json', 'config.json', 'preferences.json', '.env'}
    )


def compression_recipe() -> dict:
    return {'compression': 'lzma2/normal', 'solid': True,
            'block_threads': min(4, max(1, (os.cpu_count() or 2) // 2))}


def _compression_lines() -> list[str]:
    recipe = compression_recipe()
    return ['Compression=' + recipe['compression'], 'SolidCompression=yes',
            'LZMANumBlockThreads=' + str(recipe['block_threads'])]


def dependency_script(software, source_dir: Path, app_id: str,
                      language_file: Path, files: list[dict]) -> str:
    """A cached extractor only; the original product owns final DLL installation."""
    lines = ['[Setup]', f'AppId={directive(app_id + "-dependency-payload")}',
             'AppName=DesktopPackager Dependency Payload', 'AppVersion=1.0',
             'DefaultDirName={tmp}\\dependency-payload', 'UsePreviousAppDir=no',
             'DisableDirPage=yes', 'DisableProgramGroupPage=yes', 'Uninstallable=no',
             'PrivilegesRequired=admin', 'AlwaysRestart=no', 'RestartIfNeededByRun=no',
             'CloseApplications=no', 'RestartApplications=no', 'SetupLogging=yes',
             'OutputBaseFilename=dependencies', 'OutputDir=.', *_compression_lines(),
             '', '[Languages]',
             f'Name: "chinesesimp"; MessagesFile: {quoted(language_file, constants=True)}', '', '[Files]']
    architecture = read_pe_architecture(source_dir / software.entry_relative)
    if architecture in ('x64', 'arm64'):
        allowed = 'x64compatible' if architecture == 'x64' else 'arm64'
        lines[1:1] = [f'ArchitecturesAllowed={allowed}', f'ArchitecturesInstallIn64BitMode={allowed}']
    for file in files:
        relative = file['path']
        parent = PureWindowsPath(relative).parent
        dest = '{app}' + ('' if str(parent) == '.' else '\\' + str(parent).replace('{', '{{'))
        lines.append(f'Source: {quoted(source_dir / Path(relative), constants=True)}; '
                     f'DestDir: {quoted(dest, constants=True)}; Hash: {quoted(file["sha256"])}; Flags: ignoreversion')
    return _script('\n'.join(lines) + '\n')


def _dependency_prepare(package: dict, files: list[dict]) -> str:
    # Temporary space is separate from the destination-volume space computed
    # by Inno for external files. The helper never writes into the final app.
    required = package['size'] + sum(file['size'] for file in files) + 64 * 1024 * 1024
    checks = []
    for file in files:
        path = '{tmp}\\dependency-payload\\' + _win(file['path']).replace('{', '{{')
        checks.append(f'''    if not FileExists(ExpandConstant({pascal(path)})) then begin
      Result := '依赖文件解压不完整，请重试。';
      exit;
    end;''')
    return f'''
[Code]
var DependencyReady: Boolean;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; FreeBytes, TotalBytes: Int64; PayloadDir, Params: String;
begin
  Result := '';
  NeedsRestart := False;
  if DependencyReady then exit;
  try
    if not GetSpaceOnDisk64(ExpandConstant('{{tmp}}'), FreeBytes, TotalBytes) then begin
      Result := '无法读取临时目录可用空间。';
      exit;
    end;
    if FreeBytes < {required} then begin
      Result := '临时磁盘空间不足，解压依赖需要至少 ' + IntToStr({required}) + ' 字节。';
      exit;
    end;
    ExtractTemporaryFile('dependency-payload.exe');
    PayloadDir := ExpandConstant('{{tmp}}\\dependency-payload');
    Params := '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /DIR="' + PayloadDir + '"' +
      ' /LOG="' + ExpandConstant('{{tmp}}\\dependency-extract.log') + '"';
    if not Exec(ExpandConstant('{{tmp}}\\dependency-payload.exe'), Params, '',
        SW_HIDE, ewWaitUntilTerminated, Code) then begin
      Result := '无法启动依赖解压程序，错误码：' + IntToStr(Code);
      exit;
    end;
    if Code <> 0 then begin
      Result := '依赖解压失败，退出码：' + IntToStr(Code);
      exit;
    end;
{chr(10).join(checks)}
    DependencyReady := True;
  except
    Result := '依赖准备失败：' + GetExceptionMessage;
  end;
end;
'''


def child_script(software, source_dir: Path, app_id: str, install_dir: str,
                 language_file: Path, files: list[str], directories: list[str],
                 desktop_shortcuts: bool = True, *, dependency_package: dict | None = None,
                 dependency_files: list[dict] | None = None) -> str:
    """Produce an independent installer from an explicit source file index."""
    shortcut_name = safe_filename(software.name) + ' (' + software.role + ')'
    lines = ['[Setup]', f'AppId={directive(app_id)}', f'AppName={directive(software.name)}',
             f'AppVersion={directive(software.version)}',
             f'UninstallDisplayName={directive(software.name + " (" + software.role + ")")}',
             'DefaultDirName={autopf}\\' + directive(install_dir),
             f'DefaultGroupName={directive(shortcut_name)}', 'UsePreviousAppDir=yes',
             'DisableProgramGroupPage=yes', 'WizardStyle=modern', 'PrivilegesRequired=admin',
             'Uninstallable=yes', *_compression_lines(),
             'AlwaysRestart=no', 'RestartIfNeededByRun=no', 'SetupLogging=yes',
             'OutputBaseFilename=child', 'OutputDir=.', '', '[Languages]',
             f'Name: "chinesesimp"; MessagesFile: {quoted(language_file, constants=True)}', '', '[Dirs]']
    architecture = read_pe_architecture(source_dir / software.entry_relative)
    if architecture in ('x64', 'arm64'):
        allowed = 'x64compatible' if architecture == 'x64' else 'arm64'
        lines[1:1] = [f'ArchitecturesAllowed={allowed}', f'ArchitecturesInstallIn64BitMode={allowed}']
    for directory in directories:
        dest = '{app}\\' + _win(directory).replace('{', '{{')
        lines.append(f'Name: {quoted(dest, constants=True)}')
    lines += ['', '[Files]']
    dependencies = {file['path']: file for file in (dependency_files or [])}
    if bool(dependencies) != bool(dependency_package):
        raise ValueError('依赖文件与依赖包必须同时提供')
    if not dependencies.keys() <= set(files):
        raise ValueError('依赖文件不属于软件文件清单')
    if dependency_package:
        lines.append(f'Source: {quoted(dependency_package["path"], constants=True)}; '
                     f'DestDir: "{{tmp}}"; DestName: "dependency-payload.exe"; '
                     f'Hash: {quoted(dependency_package["sha256"])}; Flags: dontcopy nocompression')
    for relative in files:
        path = PureWindowsPath(relative)
        parent = '' if str(path.parent) == '.' else '\\' + str(path.parent).replace('{', '{{')
        # Source values are compiler filesystem paths: Inno does not expand
        # runtime constants here, so preserve their literal braces.
        if relative in dependencies:
            file = dependencies[relative]
            source = quoted('{tmp}\\dependency-payload\\' + _win(relative).replace('{', '{{'), constants=True)
            policy = 'onlyifdoesntexist uninsneveruninstall' if is_mutable(relative) else 'ignoreversion'
            lines.append(f'Source: {source}; DestDir: {quoted("{app}" + parent, constants=True)}; '
                         f'ExternalSize: {file["size"]}; Hash: {quoted(file["sha256"])}; Flags: external {policy}')
        else:
            source = quoted(source_dir / Path(relative), constants=True)
            flags = 'onlyifdoesntexist uninsneveruninstall' if is_mutable(relative) else 'ignoreversion'
            lines.append(f'Source: {source}; DestDir: {quoted("{app}" + parent, constants=True)}; Flags: {flags}')
    entry = '{app}\\' + _win(software.entry_relative).replace('{', '{{')
    parent = PureWindowsPath(software.entry_relative).parent
    work = '{app}' + ('' if str(parent) == '.' else '\\' + str(parent).replace('{', '{{'))
    lines += ['', '[Icons]',
              f'Name: {quoted("{group}\\" + shortcut_name.replace("{", "{{"), constants=True)}; Filename: {quoted(entry, constants=True)}; WorkingDir: {quoted(work, constants=True)}']
    if desktop_shortcuts:
        lines.append(f'Name: {quoted("{autodesktop}\\" + shortcut_name.replace("{", "{{"), constants=True)}; Filename: {quoted(entry, constants=True)}; WorkingDir: {quoted(work, constants=True)}')
    if dependency_package:
        lines.append(_dependency_prepare(dependency_package, dependency_files))
    return _script('\n'.join(lines) + '\n')


def outer_script(name: str, version: str, children: list[dict], runtimes: list,
                 language_file: Path, *, runtime_names: list[str] | None = None,
                 icon_filename: str | Path | None = None) -> str:
    """A coordinator only: no shared app directory or umbrella uninstaller."""
    lines = ['[Setup]', f'AppName={directive(name)}', f'AppVersion={directive(version)}',
             'CreateAppDir=no', 'Uninstallable=no', 'WizardStyle=modern',
             'PrivilegesRequired=admin', 'DisableWelcomePage=no', 'DisableReadyPage=no',
             'AlwaysRestart=no', 'RestartIfNeededByRun=no', 'SetupLogging=yes',
             'Compression=none', 'SolidCompression=no', 'OutputBaseFilename=bundle',
             'OutputDir=.', '', '[Languages]',
             f'Name: "chinesesimp"; MessagesFile: {quoted(language_file, constants=True)}', '', '[Types]',
             'Name: "custom"; Description: "自定义安装"; Flags: iscustom', '', '[Components]']
    if icon_filename:
        # SetupIconFile is the valid Inno Setup [Setup] directive that embeds
        # the selected .ico into the generated outer installer.
        lines.insert(3, f'SetupIconFile={directive(icon_filename)}')
    for child in children:
        role = child['role']
        description = f'{ROLE_NAMES[role]} · {child["name"]} {child["version"]}' + ('（必装）' if role == 'C' else '（可选）')
        line = f'Name: "{role}"; Description: {quoted(description)}'
        if role == 'C':
            line += '; Types: custom; Flags: fixed'
        lines.append(line)
    lines += ['', '[Files]']
    tasks = []
    for index, runtime in enumerate(runtimes, 1):
        filename = f'runtime-{index:03d}{Path(runtime.path).suffix.lower()}'
        lines.append(f'Source: {quoted(runtime.path, constants=True)}; DestDir: "{{tmp}}"; DestName: "{filename}"; Flags: dontcopy nocompression')
        tasks.append({'file': filename, 'name': runtime_names[index - 1] if runtime_names is not None else Path(runtime.path).name, 'role': '',
                      'params': runtime.params, 'msi': Path(runtime.path).suffix.lower() == '.msi',
                      'codes': '|'.join(str(code) for code in runtime.success_codes), 'dir': '', 'key': '', 'native64': False})
    for child in children:
        filename = f'app-{child["role"]}.exe'
        checksum = f'; Hash: {quoted(child["sha256"])}' if child.get('sha256') else ''
        lines.append(f'Source: {quoted(child["path"], constants=True)}; DestDir: "{{tmp}}"; DestName: "{filename}"{checksum}; Flags: dontcopy nocompression')
        tasks.append({'file': filename, 'name': ROLE_NAMES[child['role']] + ' · ' + child['name'] + ' ' + child['version'], 'role': child['role'],
                      'params': '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010',
                      'msi': False, 'codes': '0|3010', 'key': child.get('app_id', ''),
                      'native64': child.get('architecture') in ('x64', 'arm64'),
                      'dir': '{autopf}\\' + child.get('install_dir', safe_filename(child['name']) + '-' + child['role']).replace('{', '{{')})
    lines += ['', '[Code]', _OUTER_CODE]
    assignments = []
    for index, task in enumerate(tasks):
        for array, key in [('TaskFiles', 'file'), ('TaskNames', 'name'), ('TaskRoles', 'role'), ('TaskParams', 'params')]:
            assignments.append(f'  {array}[{index}] := {pascal(task[key])};')
        assignments.append(f'  TaskCodes[{index}] := {pascal("|" + task["codes"] + "|")};')
        assignments.append(f'  TaskMSI[{index}] := {str(task["msi"]).lower()};')
        assignments.append(f'  TaskKeys[{index}] := {pascal(task["key"])};')
        assignments.append(f'  TaskDirs[{index}] := ExpandConstant({pascal(task["dir"])});')
        if task['native64']:
            assignments.append(f'  if IsWin64 then TaskDirs[{index}] := ExpandConstant({pascal(task["dir"].replace("{autopf}", "{autopf64}", 1))});')
    initialize = f'''procedure InitializeWizard;
var I, ComponentIndex: Integer; Installed: String;
begin
  SetArrayLength(TaskFiles, {len(tasks)});
  SetArrayLength(TaskNames, {len(tasks)});
  SetArrayLength(TaskRoles, {len(tasks)});
  SetArrayLength(TaskParams, {len(tasks)});
  SetArrayLength(TaskCodes, {len(tasks)});
  SetArrayLength(TaskDirs, {len(tasks)});
  SetArrayLength(TaskKeys, {len(tasks)});
  SetArrayLength(TaskMSI, {len(tasks)});
  SetArrayLength(TaskDone, {len(tasks)});
{chr(10).join(assignments)}
  ComponentIndex := 0;
  for I := 0 to GetArrayLength(TaskNames) - 1 do
    if TaskRoles[I] <> '' then begin
      Installed := FindInstalledPath(TaskKeys[I]);
      if Installed <> '' then begin
        TaskDirs[I] := Installed;
        WizardForm.ComponentsList.ItemCaption[ComponentIndex] := WizardForm.ComponentsList.ItemCaption[ComponentIndex] + '（已安装）';
      end;
      ComponentIndex := ComponentIndex + 1;
    end;
  InstallLogDir := ExpandConstant('{{localappdata}}\\DesktopPackager\\InstallLogs\\') + GetDateTimeString('yyyymmdd-hhnnss', '-', ':');
  ForceDirectories(InstallLogDir);
  ProgressPage := CreateOutputProgressPage('正在安装所选软件', '');
end;
'''
    lines.append(initialize)
    return _script('\n'.join(lines) + '\n')


_OUTER_CODE = r'''
var
  TaskFiles, TaskNames, TaskRoles, TaskParams, TaskCodes, TaskDirs, TaskKeys: TArrayOfString;
  TaskMSI, TaskDone: array of Boolean;
  ProgressPage: TOutputProgressWizardPage;
  InstallLogDir, Summary, Failure: String;
  RestartRequired, RunningTask, CancelRequested: Boolean;

function FindInstalledPath(const AppId: String): String;
var Key: String;
begin
  Result := '';
  if AppId = '' then exit;
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + AppId + '_is1';
  if RegQueryStringValue(HKLM32, Key, 'Inno Setup: App Path', Result) then exit;
  if IsWin64 then RegQueryStringValue(HKLM64, Key, 'Inno Setup: App Path', Result);
end;

procedure RecordResult(const Text: String);
begin
  Log(Text);
  SaveStringToFile(InstallLogDir + '\result.log', Text + #13#10, True);
end;

procedure RecordOptionalRuntimeFailure(const TaskName, Detail: String);
begin
  { VC/.NET and other prerequisites are optional. Their own cancellation or
    failure is recorded for review, then the next task is still attempted. }
  RecordResult('Optional runtime skipped: ' + TaskName + '; ' + Detail);
  Summary := Summary + TaskName + ': skipped (optional runtime)' + #13#10;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if RunningTask then begin
    CancelRequested := True;
    Cancel := False;
    Confirm := False;
    ProgressPage.SetText('正在等待当前安装程序退出，之后取消后续安装。', '');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I, Code: Integer;
  Filename, Params, LogFile: String;
  Started: Boolean;
begin
  Result := '';
  NeedsRestart := False;
  if Failure <> '' then begin
    Result := Failure;
    exit;
  end;
  ProgressPage.Show;
  WizardForm.CancelButton.Enabled := True;
  try
    for I := 0 to GetArrayLength(TaskFiles) - 1 do begin
      if CancelRequested then begin
        Failure := '安装已取消。已完成的软件保留，后续软件未执行。';
        break;
      end;
      if not TaskDone[I] then begin
        if (TaskRoles[I] = '') or WizardIsComponentSelected(TaskRoles[I]) then begin
          ProgressPage.SetText('正在安装：' + TaskNames[I], '日志：' + InstallLogDir);
          ProgressPage.SetProgress(I, GetArrayLength(TaskFiles));
          ExtractTemporaryFile(TaskFiles[I]);
          Filename := ExpandConstant('{tmp}\') + TaskFiles[I];
          Params := TaskParams[I];
          LogFile := InstallLogDir + '\' + TaskFiles[I] + '.log';
          if TaskMSI[I] then begin
            Params := '/i "' + Filename + '" ' + Params + ' /norestart REBOOT=ReallySuppress /L*v "' + LogFile + '"';
            Filename := ExpandConstant('{sys}\msiexec.exe');
          end else if TaskRoles[I] <> '' then
            Params := Params + ' /LOG="' + LogFile + '"';
          RecordResult('开始：' + TaskNames[I] + '；参数：' + Params);
          RunningTask := True;
          try
            Started := Exec(Filename, Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, Code);
          finally
            RunningTask := False;
          end;
          if not Started then begin
            if TaskRoles[I] = '' then begin
              RecordOptionalRuntimeFailure(TaskNames[I], 'could not start: ' + SysErrorMessage(Code));
              TaskDone[I] := True;
            end else begin
              Failure := TaskNames[I] + ' ' + SysErrorMessage(Code);
              break;
            end;
          end else begin
            RecordResult(TaskNames[I] + ' exit code: ' + IntToStr(Code));
            { MSI 1602 is cancellation; 1641 means a vendor initiated a reboot.
              Neither may be overridden to success by runtime configuration. }
            if (Code = 1602) or (Code = 1641) or
               (Pos('|' + IntToStr(Code) + '|', TaskCodes[I]) = 0) then begin
              if TaskRoles[I] = '' then begin
                RecordOptionalRuntimeFailure(TaskNames[I], 'exit code ' + IntToStr(Code));
                TaskDone[I] := True;
              end else begin
                Failure := TaskNames[I] + ' installation failed or cancelled: ' + IntToStr(Code);
                break;
              end;
            end else begin
              if Code = 3010 then RestartRequired := True;
              TaskDone[I] := True;
              Summary := Summary + TaskNames[I] + ' completed' + #13#10;
            end;
          end;
        end;
      end;
    end;
    if CancelRequested and (Failure = '') then Failure := '安装已取消，后续软件未执行。';
    if Failure <> '' then begin
      RecordResult(Failure);
      Result := Failure + #13#10 + Summary + #13#10 + '详细日志：' + InstallLogDir;
    end;
  finally
    ProgressPage.Hide;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var I: Integer;
begin
  Result := '安装顺序：' + NewLine;
  for I := 0 to GetArrayLength(TaskNames) - 1 do
    if (TaskRoles[I] = '') or WizardIsComponentSelected(TaskRoles[I]) then begin
      Result := Result + Space + TaskNames[I] + NewLine;
      if TaskDirs[I] <> '' then Result := Result + Space + Space + TaskDirs[I] + NewLine;
    end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then begin
    WizardForm.FinishedLabel.Caption := Summary + #13#10 + '详细日志：' + InstallLogDir;
    if RestartRequired then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + '安装已完成，请在方便时手动重新启动电脑。';
  end;
end;

function NeedRestart: Boolean;
begin
  { Report via the finished page and exit code, never offer automatic reboot. }
  Result := False;
end;

function GetCustomSetupExitCode: Integer;
begin
  if RestartRequired then Result := 3010 else Result := 0;
end;
'''
