"""Compile software directly from its release directory; never run installers."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import locale
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid

from . import inno, dependencies
from .models import BuildResult, BundleConfig, RuntimeInstaller
from .validation import validate_config


class BuildCancelled(Exception):
    """The current task was cancelled; existing published outputs are intact."""


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise BuildCancelled('构建已取消')


def _hash(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel is not None:
                _check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _stat_signature(value: os.stat_result) -> tuple:
    return (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino, stat.S_IFMT(value.st_mode))


def _signature(path: Path) -> tuple:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f'发布文件不支持链接或重解析点：{path}')
    return _stat_signature(value)


def _scan(source: Path, cancel: threading.Event, progress=lambda _: None) -> dict[str, tuple]:
    """Index metadata without opening file contents or resolving every path."""
    _check_cancel(cancel)
    result = {'.': _signature(source)}
    if result['.'][-1] != stat.S_IFDIR:
        raise ValueError(f'发布目录不是文件夹：{source}')
    pending = [('.', source)]
    while pending:
        _check_cancel(cancel)
        relative_dir, directory = pending.pop()
        # Recheck a directory immediately before descending, including the root.
        if _signature(directory) != result[relative_dir]:
            raise RuntimeError(f'源发布目录在索引时发生变化：{directory}')
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        for entry in entries:
            _check_cancel(cancel)
            # DirEntry.stat on Windows leaves st_ino at zero. Use one lstat so
            # entry/metadata checks share the same complete file identity.
            value = Path(entry.path).lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f'发布文件不支持链接或重解析点：{entry.path}')
            signature = _stat_signature(value)
            if signature[-1] not in (stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f'不支持的发布文件类型：{entry.path}')
            relative = entry.name if relative_dir == '.' else relative_dir + '/' + entry.name
            result[relative] = signature
            if signature[-1] == stat.S_IFDIR:
                pending.append((relative, Path(entry.path)))
            if (len(result) - 1) % 1000 == 0:
                progress(len(result) - 1)
    return result


def _copy_file(source: Path, target: Path, cancel: threading.Event) -> dict:
    before = _signature(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        opened = os.fstat(incoming.fileno())
        if (opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before[3], before[0], before[1]):
            raise RuntimeError(f'源文件在复制时发生变化：{source}')
        while chunk := incoming.read(1024 * 1024):
            _check_cancel(cancel)
            outgoing.write(chunk)
            digest.update(chunk)
    _check_cancel(cancel)
    if _signature(source) != before or target.stat().st_size != before[0]:
        raise RuntimeError(f'源文件在复制时发生变化：{source}')
    # Preserve timestamps/mode in the private copy; all hidden files are copied.
    shutil.copystat(source, target, follow_symlinks=False)
    return {'size': before[0], 'sha256': digest.hexdigest()}


def _json_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _compiler_fingerprint(compiler: Path, language_file: Path, cancel: threading.Event) -> dict:
    """Include compiler libraries and installer templates, excluding install location."""
    signatures = {compiler: _signature(compiler)}
    components = {compiler.name: _hash(compiler, cancel)}
    for path in sorted(compiler.parent.iterdir(), key=lambda entry: entry.name.casefold()):
        if path.is_file() and (path.suffix.casefold() in {'.dll', '.e32', '.e64', '.bmp', '.ico'}
                               or path.name.casefold() in {'default.isl', 'isppbuiltins.iss'}):
            before = _signature(path)
            components[path.name] = _hash(path, cancel)
            if _signature(path) != before:
                raise RuntimeError(f'编译器组件在校验时发生变化：{path}')
            signatures[path] = before
    language_before = _signature(language_file)
    language_hash = _hash(language_file, cancel)
    signatures[language_file] = language_before
    if any(_signature(path) != signature for path, signature in signatures.items()):
        raise RuntimeError('编译器组件在校验时发生变化')
    return {'compiler_components': components, 'language_sha256': language_hash}


_PROFILE_LOCK = threading.Lock()


@contextmanager
def _profile_file_lock(path: Path):
    """Prevent two independent application instances assigning competing IDs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _PROFILE_LOCK:
        with path.with_name(path.name + '.lock').open('a+b') as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                deadline = time.monotonic() + 10
                while True:
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('产品配置正在被其他构建使用，请稍后重试')
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)


def _product_profiles(software: list, path: Path) -> dict[str, dict]:
    """Identity excludes build location, output, version and bundle composition."""
    with _profile_file_lock(path):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {'schema_version': 1, 'products': {}}
            if data.get('schema_version') != 1 or not isinstance(data.get('products'), dict):
                raise ValueError('invalid schema')
            products = data['products']
            ids = set()
            for value in products.values():
                if not isinstance(value, dict) or not isinstance(value.get('app_id'), str) or not value['app_id'].strip():
                    raise ValueError('missing AppId')
                inno.directive(value['app_id'])
                if value['app_id'].casefold() in ids:
                    raise ValueError('duplicate AppId')
                ids.add(value['app_id'].casefold())
                if not isinstance(value.get('install_dir'), str) or not value['install_dir'] or inno.safe_filename(value['install_dir']) != value['install_dir']:
                    raise ValueError('invalid install directory')
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f'产品标识配置损坏，已保留原文件；请恢复备份后重试：{path}') from error
        result = {}
        for item in software:
            identity = json.dumps([item.role, item.entry_relative.name.casefold(), item.name.casefold()], ensure_ascii=False)
            key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            if key not in products:
                app_id = '{' + str(uuid.uuid4()).upper() + '}'
                products[key] = {'app_id': app_id, 'role': item.role, 'name': item.name,
                                 'entry_name': item.entry_relative.name,
                                 'install_dir': inno.safe_filename(item.name)[:70] + '-' + item.role + '-' + app_id[1:9]}
            result[item.role] = dict(products[key])
        _json_write(path, data)
        return result


def read_product_metadata(path: Path) -> dict[str, str]:
    """Read version resources without loading/executing the supplied executable."""
    if os.name != 'nt':
        return {}
    from ctypes import wintypes
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    dummy = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(dummy))
    if not size:
        return {}
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return {}
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    translations = ['040904b0', '040904e4', '080404b0']
    if version.VerQueryValueW(buffer, r'\VarFileInfo\Translation', ctypes.byref(pointer), ctypes.byref(length)):
        words = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
        translations = [f'{words[i]:04x}{words[i+1]:04x}' for i in range(0, length.value // 2 - 1, 2)] + translations
    result = {}
    for field in ('ProductName', 'FileDescription', 'ProductVersion', 'FileVersion', 'CompanyName', 'OriginalFilename'):
        for language in translations:
            key = f'\\StringFileInfo\\{language}\\{field}'
            if version.VerQueryValueW(buffer, key, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
                text = ctypes.wstring_at(pointer, length.value).rstrip('\0').strip()
                if text and not any(ord(char) < 32 for char in text):
                    result[field] = text
                    break
    return result


def _decode_line(value: bytes) -> str:
    for encoding in dict.fromkeys(('utf-8-sig', locale.getpreferredencoding(False), 'mbcs' if os.name == 'nt' else 'utf-8')):
        try:
            return value.decode(encoding).rstrip('\r\n')
        except (UnicodeDecodeError, LookupError):
            pass
    return value.decode('utf-8', errors='replace').rstrip('\r\n')


def _run_process(argv: list[str], log, cancel: threading.Event) -> int:
    """Drain merged output on a reader thread while polling cancellation."""
    _check_cancel(cancel)
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, shell=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    lines = queue.Queue()
    def read_output():
        try:
            for line in iter(process.stdout.readline, b''):
                lines.put(line)
        finally:
            lines.put(None)
    reader = threading.Thread(target=read_output, daemon=True, name='ISCC-output')
    reader.start()
    try:
        finished = False
        while not finished:
            _check_cancel(cancel)
            try:
                line = lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                finished = True
            else:
                log(_decode_line(line))
        while process.poll() is None:
            _check_cancel(cancel)
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        _check_cancel(cancel)
        return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        reader.join(timeout=3)
        process.stdout.close()


def _compile(compiler: Path, script: Path, output_dir: Path, output_name: str, log, cancel: threading.Event) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = output_dir / (output_name + '.exe')
    if expected.exists():
        raise RuntimeError(f'本次构建输出已存在，拒绝使用旧文件：{expected}')
    argv = [str(compiler), '/O' + str(output_dir), '/F' + output_name, str(script)]
    log('执行编译：' + subprocess.list2cmdline(argv))
    status = _run_process(argv, log, cancel)
    if status != 0:
        raise RuntimeError(f'Inno 编译失败（退出码 {status}）：{script.name}；请查看编译日志')
    if not expected.is_file() or expected.stat().st_size < 2:
        raise RuntimeError(f'编译器未生成本次目标安装包：{expected}')
    with expected.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise RuntimeError(f'编译产物不是有效的 Windows 可执行文件：{expected}')
    return expected


def _remove_task(path: Path) -> None:
    """Only called with the exact private mkdtemp-like build directory."""
    def writable_then_retry(function, filename, error):
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)
    if path.exists():
        shutil.rmtree(path, onerror=writable_then_retry)


def build_bundle(config: BundleConfig, *, log=lambda _: None, progress=lambda *_: None,
                 cancel: threading.Event | None = None, profile_path: Path | None = None,
                 cache_dir: Path | None = None) -> BuildResult:
    cancel = cancel if cancel is not None else threading.Event()
    _check_cancel(cancel)
    # Freeze GUI-owned mutable config at the task boundary.
    config = BundleConfig.from_dict(config.to_dict())
    progress(1, '校验输入与编译器')
    software = validate_config(config, check_files=False)
    _check_cancel(cancel)
    compiler = Path(config.compiler.strip()).resolve()
    language_file = compiler.parent / 'Languages' / 'ChineseSimplified.isl'
    if not language_file.is_file():
        raise ValueError(f'编译器缺少简体中文语言文件：{language_file}')
    profile_path = Path(profile_path).resolve() if profile_path is not None else (
        Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'DesktopPackager' / 'products.json').resolve()
    if any(profile_path.is_relative_to(item.source_dir) for item in software):
        raise ValueError('产品配置不能写入原始发布目录')
    cache_root = (Path(cache_dir) if cache_dir is not None else profile_path.parent / 'cache' / 'dependencies').resolve()
    if any(cache_root.is_relative_to(item.source_dir) for item in software):
        raise ValueError('依赖包缓存不能写入原始发布目录')
    metadata = {}
    metadata_signatures = {}
    resolved = []
    for item in software:
        before = _signature(item.entry_path)
        fields = read_product_metadata(item.entry_path)
        if _signature(item.entry_path) != before:
            raise RuntimeError(f'源入口在读取产品信息时发生变化：{item.entry_path}')
        metadata_signatures[item.role] = before
        metadata[item.role] = fields
        product_version = fields.get('ProductVersion', fields.get('FileVersion', ''))
        match = re.match(r'\d+(?:\.\d+){0,3}', product_version.replace(',', '.').replace(' ', ''))
        resolved.append(replace(item, name=fields.get('ProductName', fields.get('FileDescription', item.entry_relative.stem)),
                                version=match.group(0) if match else '1.0.0'))
    software = resolved
    profiles = _product_profiles(software, profile_path)
    _check_cancel(cancel)
    output = Path(config.output.strip()).resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10]
    task = output / ('.build-' + build_id)
    task.mkdir(exist_ok=False)
    artifacts = task / 'artifacts'
    scripts, logs = artifacts / 'scripts', artifacts / 'logs'
    scripts.mkdir(parents=True)
    logs.mkdir()
    artifact_dir = output / ('build-' + build_id)
    output_file = output / (inno.safe_filename(config.name) + '-' + inno.safe_filename(config.version) + '-' + build_id + '-Bundle.exe')
    manifest = {'schema_version': 2, 'build_id': build_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                'input_mode': 'direct_source', 'source_integrity': 'metadata_before_after',
                'status': 'building', 'config': config.to_dict(), 'compiler': str(compiler),
                'software': [], 'runtimes': [], 'output': None,
                'scripts_purpose': '主软件脚本引用原始目录及已校验的依赖包；总包脚本引用任务载荷。重建请导入 config.json、products.json，并提供原始发布文件及环境包。所选 DLL 有逐次 SHA-256，其余软件输入元数据不构成内容哈希证据。',
                'warnings': ['运行环境 EXE 参数由厂商定义；未知 EXE 的 /norestart 行为需要在测试机确认。',
                             '用户配置按文件类型及常见配置目录保留；真实产品的数据/升级规则需独立验收。']}
    build_log = (logs / 'build.log').open('w', encoding='utf-8', newline='\n')
    def emit(message):
        build_log.write(str(message) + '\n')
        build_log.flush()
        log(str(message))
    def stage(percent, message):
        _check_cancel(cancel)
        emit(message)
        progress(percent, message)
    def compile_logged(script, target_dir, filename):
        with (logs / (filename + '.log')).open('w', encoding='utf-8', newline='\n') as compiler_log:
            def compiler_emit(message):
                compiler_log.write(message + '\n')
                compiler_log.flush()
                emit(message)
            return _compile(compiler, script, target_dir, filename, compiler_emit, cancel)
    published = False
    dependency_candidates = []
    dependency_records = {}
    cache_activations = []
    compiler_fingerprint = None
    try:
        _json_write(artifacts / 'config.json', config.to_dict())
        (artifacts / 'README.txt').write_text(
            '构建记录与重建说明\n\nmanifest.json 记录软件输入的相对路径、大小及修改时间，以及环境包与实际输出的 SHA-256。\n'
            '软件直接从所选目录编译，不复制完整目录；仅人工勾选的 DLL 每次计算 SHA-256，其余文件建立元数据索引。\n'
            'dependencies/ 保存成功依赖包记录，包本体在应用数据缓存中；通过界面保存记录可携带包一起迁移。\n'
            '编译前后检查文件集合及元数据；此检查无法识别保留元数据的所有内容变化。\n'
            'products.json 是本次产品的稳定 AppId 配置；换电脑时请与软件发布目录一起保存迁移。\n'
            'config.json 可导入打包工具，路径迁移后应重新选择完整发布目录。\n'
            'scripts/ 下的子脚本引用原始软件路径，总包脚本引用已清理的子安装器及环境包路径。\n'
            '重建时请导入 config.json，提供原始发布文件和运行环境安装包，再使用同一 products.json 构建。仅凭软件输入元数据不能保证精确重建。\n'
            '本工具不会运行生成的安装器；安装、升级、用户数据保留及卸载仍需在独立测试机验收。\n', encoding='utf-8')
        _json_write(artifacts / 'products.json', {'schema_version': 1, 'products': {
            hashlib.sha256(json.dumps([item.role, item.entry_relative.name.casefold(), item.name.casefold()], ensure_ascii=False).encode('utf-8')).hexdigest(): profiles[item.role]
            for item in software}})
        children = []
        for index, item in enumerate(software):
            stage(5 + index * 12, f'索引软件 {item.role}：{item.name}')
            if _signature(item.entry_path) != metadata_signatures[item.role]:
                raise RuntimeError(f'源入口在构建期间发生变化：{item.entry_path}')
            before = _scan(item.source_dir, cancel, lambda count: stage(
                5 + index * 12, f'索引软件 {item.role}：已扫描 {count:,} 项'))
            if before.get(item.entry_relative.as_posix()) != metadata_signatures[item.role]:
                raise RuntimeError(f'源入口在索引期间发生变化：{item.entry_path}')
            files = [{'path': relative, 'size': signature[0], 'mtime_ns': signature[1],
                      'preserve_on_upgrade': inno.is_mutable(relative)}
                     for relative, signature in before.items() if signature[-1] == stat.S_IFREG]
            directories = [relative for relative, signature in before.items()
                           if relative != '.' and signature[-1] == stat.S_IFDIR]
            profile = profiles[item.role]
            architecture = inno.read_pe_architecture(item.entry_path)
            manifest['software'].append({'role': item.role, 'name': item.name, 'version': item.version,
                                         'source_dir': str(item.source_dir), 'entry_relative': item.entry_relative.as_posix(),
                                         'app_id': profile['app_id'], 'install_dir': profile['install_dir'],
                                         'entry_architecture': architecture,
                                         'metadata': metadata[item.role], 'files': files, 'directories': directories})
            selected = config.dependency_files.get(item.role, [])
            dependency_files = []
            dependency_package = None
            if selected:
                manifest['selected_dll_integrity'] = 'sha256_every_build'
                actual_paths = {relative.casefold(): relative for relative in before}
                if len(actual_paths) != len(before):
                    raise ValueError('发布目录包含 Windows 大小写冲突路径')
                normalized_selected = []
                for relative in selected:
                    relative = dependencies.normalize_path(relative, dll_only=True)
                    actual = actual_paths.get(relative.casefold())
                    if actual is None or before[actual][-1] != stat.S_IFREG:
                        raise ValueError(f'所选 DLL 不存在或不是文件：{relative}')
                    normalized_selected.append(actual)
                selected = normalized_selected
                stage(7 + index * 12, f'校验软件 {item.role} 所选 DLL（{len(selected)} 个，{config.hash_workers} 线程）')
                dependency_files = dependencies.hash_selected(
                    item.source_dir, selected, cancel, workers=config.hash_workers,
                    progress=lambda done, total, count, all_count: stage(
                        7 + index * 12, f'校验软件 {item.role} DLL：{count}/{all_count}，{done / 1048576:.1f}/{total / 1048576:.1f} MB'))
                for file in dependency_files:
                    if file['path'] not in before or before[file['path']][-1] != stat.S_IFREG:
                        raise RuntimeError(f'所选 DLL 在构建时发生变化：{file["path"]}')
                selected_hashes = {file['path']: file['sha256'] for file in dependency_files}
                for file in files:
                    if file['path'] in selected_hashes:
                        file['sha256'] = selected_hashes[file['path']]
                if compiler_fingerprint is None:
                    compiler_fingerprint = _compiler_fingerprint(compiler, language_file, cancel)
                recipe = {'generator': 'dll-temp-extractor-v1', 'product_app_id': profile['app_id'],
                          'install_dir': profile['install_dir'], 'architecture': architecture,
                          'path_map': 'root-relative-v1', 'payload_flags': 'ignoreversion',
                          'final_flags': 'external with original mutable-policy-v1', 'compression': inno.compression_recipe(),
                          **compiler_fingerprint}
                key = dependencies.cache_key(dependency_files, recipe)
                imported = None
                record_path = config.dependency_records.get(item.role, '')
                if record_path:
                    try:
                        imported = dependencies.load_record(Path(record_path), expected_role=item.role)
                    except (OSError, ValueError) as error:
                        emit(f'软件 {item.role} 旧依赖记录不可用，将按当前文件检查缓存或重建：{error}')
                cache = dependencies.DependencyCache(cache_root)
                record = cache.find(key, dependency_files, recipe, cancel, imported_record=imported)
                reused = record is not None
                candidate_exe = None
                if reused:
                    dependency_package = record['package']
                    stage(9 + index * 12, f'软件 {item.role} DLL 内容一致，复用依赖子包')
                else:
                    stage(9 + index * 12, f'软件 {item.role} 依赖内容、选择或缓存变化，重建依赖子包')
                    dependency_source = scripts / ('dependencies-' + item.role + '.iss')
                    dependency_source.write_text(inno.dependency_script(
                        item, item.source_dir, profile['app_id'], language_file, dependency_files), encoding='utf-8-sig')
                    candidate_exe = compile_logged(dependency_source, task / 'compiled' / 'dependencies', 'dependencies-' + item.role)
                    dependency_package = {'path': str(candidate_exe), 'size': candidate_exe.stat().st_size,
                                          'sha256': _hash(candidate_exe, cancel)}
                    record = {'schema_version': 1, 'kind': 'dependency_package', 'role': item.role,
                              'source_hint': str(item.source_dir), 'files': dependency_files,
                              'recipe': recipe, 'cache_key': key, 'package': dependency_package}
                dependency_candidates.append((item.role, cache, record, candidate_exe))
                manifest['software'][-1]['dependency'] = {'cache_key': key, 'reused': reused,
                                                          'files': dependency_files,
                                                          'package_sha256': dependency_package['sha256'],
                                                          'recipe': recipe}
            script = scripts / ('child-' + item.role + '.iss')
            script.write_text(inno.child_script(item, item.source_dir, profile['app_id'], profile['install_dir'], language_file,
                                                [file['path'] for file in files], directories, config.desktop_shortcuts,
                                                dependency_package=dependency_package, dependency_files=dependency_files), encoding='utf-8-sig')
            stage(10 + index * 12, f'编译软件 {item.role} 子安装器')
            child = compile_logged(script, task / 'compiled' / 'children', 'child-' + item.role)
            stage(15 + index * 12, f'检查软件 {item.role} 源目录变化')
            if _scan(item.source_dir, cancel, lambda count: stage(
                    15 + index * 12, f'检查软件 {item.role}：已扫描 {count:,} 项')) != before:
                raise RuntimeError(f'源发布目录在编译时发生变化，请保持目录不变后重试：{item.source_dir}')
            manifest['software'][-1]['installer'] = {'size': child.stat().st_size, 'sha256': _hash(child, cancel)}
            children.append({'role': item.role, 'name': item.name, 'version': item.version,
                             'architecture': manifest['software'][-1]['entry_architecture'],
                             'path': child, 'app_id': profile['app_id'], 'install_dir': profile['install_dir'],
                             'sha256': manifest['software'][-1]['installer']['sha256']})
        stage(48, '收集并校验运行环境安装包')
        runtimes = []
        for index, runtime in enumerate(config.runtimes, 1):
            _check_cancel(cancel)
            source = runtime.path.resolve()
            bundled_name = f'runtime-{index:03d}{source.suffix.lower()}'
            target = task / 'payload' / 'runtimes' / bundled_name
            signature = _signature(source)
            copied = _copy_file(source, target, cancel)
            if _signature(source) != signature or _hash(source, cancel) != copied['sha256']:
                raise RuntimeError(f'运行环境文件在复制时发生变化：{source}')
            manifest['runtimes'].append({'source': str(source), 'bundled_name': bundled_name,
                                          'params': runtime.params, 'success_codes': list(runtime.success_codes), **copied})
            runtimes.append(RuntimeInstaller(target, runtime.params, runtime.success_codes))
        stage(60, '生成总安装器：环境优先、按选择串行安装')
        outer = scripts / 'bundle.iss'
        outer.write_text(inno.outer_script(config.name, config.version, children, runtimes, language_file,
                                          runtime_names=[Path(item['source']).name for item in manifest['runtimes']],
                                          icon_filename=config.icon_path or None), encoding='utf-8-sig')
        stage(65, '编译总安装包')
        bundle = compile_logged(outer, task / 'compiled', 'bundle')
        stage(93, '校验产物与 SHA-256')
        manifest['output'] = {'filename': output_file.name, 'size': bundle.stat().st_size, 'sha256': _hash(bundle, cancel)}
        if dependency_candidates:
            stage(95, '保存成功依赖包与哈希记录')
            for role, cache, record, candidate_exe in dependency_candidates:
                _check_cancel(cancel)
                if candidate_exe is not None:
                    expected_package = record['package']
                    record = cache.publish(record['cache_key'], record['files'], record['recipe'],
                                           role, record['source_hint'], candidate_exe, cancel, activate=False)
                    if (record['package']['size'], record['package']['sha256']) != (
                            expected_package['size'], expected_package['sha256']):
                        raise RuntimeError('依赖包在缓存发布时发生变化，已停止本次构建')
                    cache_activations.append((cache, record))
                dependencies.save_record(artifacts / 'dependencies' / (role + '.json'), record, cancel=cancel)
                dependency_records[role] = str(artifact_dir / 'dependencies' / (role + '.json'))
            manifest['dependency_records'] = dict(dependency_records)
        manifest['status'] = 'success'
        manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
        stage(98, '发布安装包和构建记录')
        if output_file.exists() or artifact_dir.exists():
            raise RuntimeError('本次目标已存在，拒绝覆盖旧产物')
        emit('最终安装包：' + str(output_file))
        build_log.close()
        _check_cancel(cancel)
        manifest['artifacts'] = [{'path': path.relative_to(artifacts).as_posix(), 'size': path.stat().st_size, 'sha256': _hash(path, cancel)}
                                 for path in sorted(artifacts.rglob('*')) if path.is_file()]
        _json_write(artifacts / 'manifest.json', manifest)
        _check_cancel(cancel)
        os.replace(artifacts, artifact_dir)
        _check_cancel(cancel)
        # The executable becomes visible only after compilation, hash checking,
        # and complete artifact publication. Never consider another .exe a result.
        os.replace(bundle, output_file)
        published = True
        # From here publication is committed. A cancelled task never activates
        # a new cache baseline, and cache bookkeeping cannot invalidate an EXE
        # that is already successfully published and verified.
        for cache, record in cache_activations:
            try:
                cache.activate(record)
            except (OSError, ValueError, RuntimeError) as error:
                log(f'安装包已生成，依赖缓存索引更新失败；可从本次构建记录导入依赖包：{error}')
        progress(100, '打包完成')
        return BuildResult(output_file, artifact_dir, artifact_dir / 'manifest.json', build_id,
                           [item.role for item in software], len(runtimes), dependency_records)
    except BaseException as error:
        if not published:
            manifest['status'] = 'cancelled' if isinstance(error, BuildCancelled) else 'failed'
            manifest['error'] = str(error)
            manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
            evidence = artifact_dir if artifact_dir.exists() else artifacts
            try:
                if not build_log.closed:
                    build_log.write(manifest['status'] + ': ' + str(error) + '\n')
                    build_log.flush()
                    build_log.close()
                _json_write(evidence / 'manifest.json', manifest)
                if evidence == artifacts:
                    os.replace(artifacts, artifact_dir)
                log('构建记录：' + str(artifact_dir))
            except OSError:
                # Preserve the original failure; task cleanup never targets any
                # source directory or previously published output.
                pass
        raise
    finally:
        if not build_log.closed:
            build_log.close()
        _remove_task(task)
