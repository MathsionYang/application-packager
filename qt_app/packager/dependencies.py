"""DLL content verification, portable records, and immutable extractor caches.

These functions never execute an installer. Metadata scans do not open payloads;
selected DLLs are streamed again on every hash_selected call.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import threading
import time
import uuid

from .models import ROLE_ORDER

_CHUNK_SIZE = 1024 * 1024
_BATCH_SIZE = 500
_SHA256 = re.compile(r'[0-9a-fA-F]{64}')


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        # builder imports this module; defer this import until execution.
        from .builder import BuildCancelled
        raise BuildCancelled('构建已取消')


def normalize_path(value: str, *, dll_only: bool = False) -> str:
    """Validate a complete Windows-relative path, preserving distinct directories."""
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('依赖路径必须为非空相对路径')
    text = value.replace('\\', '/')
    windows = PureWindowsPath(text)
    parts = text.split('/')
    if windows.drive or windows.root or any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'依赖路径不能为绝对路径、空段或越界路径：{value}')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if any(re.search(r'[<>:"|?*]', part) or part.endswith(('.', ' '))
           or part.split('.')[0].casefold() in reserved for part in parts):
        raise ValueError(f'不支持的 Windows 文件路径：{value}')
    if dll_only and windows.suffix.casefold() != '.dll':
        raise ValueError(f'第三方依赖仅支持 DLL：{value}')
    return text


def _identity(value: os.stat_result) -> tuple:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, stat.S_IFMT(value.st_mode))


def _opened_identity(value: os.stat_result) -> tuple:
    # Python 3.14 on Windows can report different ctime semantics from lstat
    # and fstat. Compare the shared identity fields when checking an open handle.
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, stat.S_IFMT(value.st_mode))


def _safe_stat(path: Path) -> os.stat_result:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f'依赖文件不支持链接或重解析点：{path}')
    return value


def _root(path: Path) -> Path:
    root = Path(path).absolute()
    if not stat.S_ISDIR(_safe_stat(root).st_mode):
        raise ValueError(f'软件发布目录不是文件夹：{root}')
    return root


def _checked_file(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    if not stat.S_ISDIR(_safe_stat(root).st_mode):
        raise ValueError('软件发布目录已变化')
    current = root
    parts = relative.split('/')
    for part in parts[:-1]:
        current = current / part
        if not stat.S_ISDIR(_safe_stat(current).st_mode):
            raise ValueError(f'依赖路径中的目录不是文件夹：{current}')
    path = current / parts[-1]
    value = _safe_stat(path)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f'依赖输入不是普通文件：{path}')
    return path, value


def scan_files(root: Path, cancel: threading.Event, on_batch=None) -> list[dict]:
    """Enumerate files using metadata only; callbacks receive batches of <=500."""
    _check_cancel(cancel)
    root = _root(root)
    result, batch = [], []
    pending = [(root, '', _identity(_safe_stat(root)))]
    seen = set()
    while pending:
        _check_cancel(cancel)
        directory, prefix, before = pending.pop()
        if _identity(_safe_stat(directory)) != before:
            raise RuntimeError(f'源目录在扫描时发生变化：{directory}')
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        for entry in entries:
            _check_cancel(cancel)
            path = Path(entry.path)
            value = _safe_stat(path)
            relative = normalize_path(prefix + entry.name)
            if relative.casefold() in seen:
                raise ValueError(f'发布目录包含冲突路径：{relative}')
            seen.add(relative.casefold())
            if stat.S_ISDIR(value.st_mode):
                pending.append((path, relative + '/', _identity(value)))
            elif stat.S_ISREG(value.st_mode):
                item = {'path': relative, 'size': value.st_size, 'mtime_ns': value.st_mtime_ns}
                result.append(item)
                batch.append(item)
                if len(batch) == _BATCH_SIZE:
                    if on_batch is not None:
                        on_batch(list(batch))
                    batch.clear()
            else:
                raise ValueError(f'不支持的发布文件类型：{relative}')
    _check_cancel(cancel)
    if batch and on_batch is not None:
        on_batch(list(batch))
    _check_cancel(cancel)
    return result


def hash_selected(root: Path, paths, cancel: threading.Event, workers: int = 2, progress=None) -> list[dict]:
    """Stream every selected DLL, with bounded workers and four-value progress."""
    _check_cancel(cancel)
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('哈希并发数必须为 1 到 8 的整数')
    if not isinstance(paths, (list, tuple)):
        raise ValueError('依赖选择必须为路径列表')
    root = _root(root)
    relatives = sorted((normalize_path(path, dll_only=True) for path in paths), key=lambda path: (path.casefold(), path))
    if len({path.casefold() for path in relatives}) != len(relatives):
        raise ValueError('依赖文件路径重复')
    inputs = []
    for relative in relatives:
        _check_cancel(cancel)
        path, before = _checked_file(root, relative)
        inputs.append((relative, path, before))
    total_bytes, total_files = sum(item[2].st_size for item in inputs), len(inputs)
    done_bytes = done_files = 0
    lock, stopped = threading.Lock(), threading.Event()
    if progress is not None:
        progress(0, total_bytes, 0, total_files)
    _check_cancel(cancel)

    def check_running():
        _check_cancel(cancel)
        if stopped.is_set():
            raise RuntimeError('其他依赖任务已停止')

    def run(item):
        nonlocal done_bytes, done_files
        relative, path, before = item
        check_running()
        _, current = _checked_file(root, relative)
        if _identity(current) != _identity(before):
            raise RuntimeError(f'源 DLL 在哈希前发生变化：{relative}')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            if _opened_identity(os.fstat(stream.fileno())) != _opened_identity(before):
                raise RuntimeError(f'源 DLL 在打开时发生变化：{relative}')
            while chunk := stream.read(_CHUNK_SIZE):
                check_running()
                digest.update(chunk)
                with lock:
                    done_bytes += len(chunk)
        check_running()
        try:
            _, after = _checked_file(root, relative)
        except (OSError, ValueError) as error:
            raise RuntimeError(f'源 DLL 在哈希时发生变化：{relative}') from error
        if _identity(after) != _identity(before):
            raise RuntimeError(f'源 DLL 在哈希时发生变化：{relative}')
        with lock:
            done_files += 1
        check_running()
        return {'path': relative, 'size': before.st_size, 'mtime_ns': before.st_mtime_ns,
                'sha256': digest.hexdigest()}

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='DLL-SHA256')
    remaining, result = iter(inputs), []
    pending = set()
    last_progress = time.monotonic()

    def emit_progress(force=False):
        nonlocal last_progress
        now = time.monotonic()
        if progress is not None and (force or now - last_progress >= 0.1):
            with lock:
                values = (done_bytes, total_bytes, done_files, total_files)
            # Only the caller/controller thread invokes callbacks, including Qt
            # signal and build-log callbacks. Workers only update counters.
            progress(*values)
            last_progress = now

    try:
        for _ in range(min(workers, total_files)):
            pending.add(executor.submit(run, next(remaining)))
        while pending:
            _check_cancel(cancel)
            completed, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in completed:
                result.append(future.result())
                following = next(remaining, None)
                if following is not None:
                    pending.add(executor.submit(run, following))
            emit_progress()
        emit_progress(force=True)
        _check_cancel(cancel)
        # Also detect changes after an early worker finished, before the whole
        # group is handed to the compiler (including changes from a callback).
        for relative, path, before in inputs:
            _check_cancel(cancel)
            try:
                _, after = _checked_file(root, relative)
            except (OSError, ValueError) as error:
                raise RuntimeError(f'源 DLL 在哈希时发生变化：{relative}') from error
            if _identity(after) != _identity(before):
                raise RuntimeError(f'源 DLL 在哈希时发生变化：{relative}')
        return sorted(result, key=lambda item: (item['path'].casefold(), item['path']))
    finally:
        stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)


def _sha(value, label='SHA-256') -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f'{label} 格式无效')
    return value.lower()


def _integer(value, label) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f'{label} 必须为非负整数')
    return value


def _files(files, require_hash=True) -> list[dict]:
    if not isinstance(files, list):
        raise ValueError('依赖记录 files 必须为列表')
    normalized, seen = [], set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('依赖文件记录格式错误')
        path = normalize_path(item.get('path'), dll_only=True)
        if path.casefold() in seen:
            raise ValueError('依赖记录包含重复路径')
        seen.add(path.casefold())
        value = {'path': path, 'size': _integer(item.get('size'), '文件大小'),
                 'mtime_ns': _integer(item.get('mtime_ns', 0), '文件修改时间')}
        if require_hash or 'sha256' in item:
            value['sha256'] = _sha(item.get('sha256'))
        normalized.append(value)
    return sorted(normalized, key=lambda item: (item['path'].casefold(), item['path']))


def _canonical_recipe(recipe) -> dict:
    if not isinstance(recipe, dict):
        raise ValueError('依赖包 recipe 必须为对象')
    try:
        # Round-trip also prevents mutable aliases from changing published data.
        return json.loads(json.dumps(recipe, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError('依赖包 recipe 必须为有效 JSON 对象') from error


def cache_key(files, recipe) -> str:
    normalized = _files(files)
    payload = {'files': [{'path': item['path'], 'size': item['size'], 'sha256': item['sha256']}
                         for item in normalized], 'recipe': _canonical_recipe(recipe)}
    value = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _validate_record(record, base: Path, expected_role=None) -> dict:
    if not isinstance(record, dict) or type(record.get('schema_version')) is not int or record['schema_version'] != 1:
        raise ValueError('不支持的依赖记录版本')
    kind, role = record.get('kind'), record.get('role')
    if kind not in ('selection', 'dependency_package') or role not in ROLE_ORDER:
        raise ValueError('依赖记录类型或软件角色无效')
    if expected_role is not None and role != expected_role:
        raise ValueError(f'依赖记录属于软件 {role}，不能用于软件 {expected_role}')
    hint = record.get('source_hint', '')
    if not isinstance(hint, str) or any(ord(c) < 32 for c in hint):
        raise ValueError('依赖记录源目录提示无效')
    result = {'schema_version': 1, 'kind': kind, 'role': role, 'source_hint': hint,
              'files': _files(record.get('files'), require_hash=kind == 'dependency_package'),
              'recipe': None, 'cache_key': None, 'package': None}
    if kind == 'selection':
        if any(record.get(field) is not None for field in ('recipe', 'cache_key', 'package')):
            raise ValueError('选择记录不能冒充已生成依赖包')
        return result
    if not result['files']:
        raise ValueError('依赖包记录不能为空')
    result['recipe'] = _canonical_recipe(record.get('recipe'))
    result['cache_key'] = _sha(record.get('cache_key'), '缓存键')
    if result['cache_key'] != cache_key(result['files'], result['recipe']):
        raise ValueError('依赖记录缓存键与文件或安装规则不一致')
    package = record.get('package')
    if not isinstance(package, dict) or not isinstance(package.get('path'), str) or not package['path']:
        raise ValueError('依赖包路径无效')
    raw_path = package['path']
    if any(ord(c) < 32 for c in raw_path):
        raise ValueError('依赖包路径无效')
    path = Path(raw_path)
    if not path.is_absolute():
        raw_path = normalize_path(raw_path)
        path = base / Path(raw_path)
    result['package'] = {'path': str(path.absolute()), 'size': _integer(package.get('size'), '依赖包大小'),
                         'sha256': _sha(package.get('sha256'), '依赖包 SHA-256')}
    return result


def load_record(path: Path, expected_role=None) -> dict:
    path = Path(path).absolute()
    try:
        with path.open('r', encoding='utf-8-sig') as stream:
            raw = json.load(stream)
        return _validate_record(raw, path.parent, expected_role)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f'依赖记录不是有效 JSON：{path}') from error


def _atomic_json(path: Path, data, cancel=None) -> None:
    _check_cancel(cancel)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _check_cancel(cancel)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _package_digest(path: Path, cancel=None, copy_to: Path | None = None) -> tuple[int, str]:
    """Verify bytes while copying; never modify the original cache generation."""
    _check_cancel(cancel)
    before = _safe_stat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('依赖安装包不是普通文件')
    digest, size = hashlib.sha256(), 0
    output = None
    try:
        if copy_to is not None:
            copy_to.parent.mkdir(parents=True, exist_ok=True)
            output = copy_to.open('xb')
        with path.open('rb') as stream:
            if _opened_identity(os.fstat(stream.fileno())) != _opened_identity(before):
                raise RuntimeError('依赖安装包在打开时发生变化')
            if stream.read(2) != b'MZ':
                raise ValueError('依赖安装包不是 Windows EXE')
            stream.seek(0)
            while chunk := stream.read(_CHUNK_SIZE):
                _check_cancel(cancel)
                digest.update(chunk)
                size += len(chunk)
                if output is not None:
                    output.write(chunk)
        _check_cancel(cancel)
        if _identity(_safe_stat(path)) != _identity(before):
            raise RuntimeError('依赖安装包在校验时发生变化')
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
        return size, digest.hexdigest()
    finally:
        if output is not None:
            output.close()


def _verify_package(record, cancel) -> bool:
    try:
        package = record['package']
        path = Path(package['path'])
        if _safe_stat(path).st_size != package['size']:
            return False
        size, digest = _package_digest(path, cancel)
        return size == package['size'] and digest == package['sha256']
    except (OSError, ValueError, RuntimeError):
        _check_cancel(cancel)
        return False


def save_record(path: Path, record: dict, portable: bool = False, cancel=None) -> None:
    path = Path(path).absolute()
    _check_cancel(cancel)
    validated = _validate_record(record, path.parent)
    temporary = None
    try:
        if portable and validated['kind'] == 'dependency_package':
            package = validated['package']
            key = validated['cache_key']
            parent = path.parent / (path.stem + '.files') / key
            generation = uuid.uuid4().hex
            temporary, final = parent / ('.tmp-' + generation), parent / generation
            temporary.mkdir(parents=True, exist_ok=False)
            size, digest = _package_digest(Path(package['path']), cancel, temporary / 'dependencies.exe')
            if (size, digest) != (package['size'], package['sha256']):
                raise ValueError('依赖包 SHA-256 与记录不一致，未导出')
            _check_cancel(cancel)
            os.replace(temporary, final)
            temporary = None
            validated['package']['path'] = (final / 'dependencies.exe').relative_to(path.parent).as_posix()
        _atomic_json(path, validated, cancel)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


class DependencyCache:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def find(self, key, files, recipe, cancel, imported_record=None) -> dict | None:
        _check_cancel(cancel)
        key = _sha(key, '缓存键')
        if key != cache_key(files, recipe):
            raise ValueError('请求的缓存键与当前 DLL 不一致')
        candidates = []
        if imported_record is not None:
            try:
                candidates.append(_validate_record(imported_record, self.root))
            except ValueError:
                pass
        pointer = self.root / key / 'latest.json'
        try:
            candidates.append(load_record(pointer))
        except (OSError, ValueError):
            pass
        for record in candidates:
            _check_cancel(cancel)
            if record['kind'] == 'dependency_package' and record['cache_key'] == key and _verify_package(record, cancel):
                return deepcopy(record)
        return None

    def publish(self, key, files, recipe, role, source_hint, exe, cancel, *, activate=True) -> dict:
        _check_cancel(cancel)
        key = _sha(key, '缓存键')
        if key != cache_key(files, recipe):
            raise ValueError('发布的缓存键与 DLL 或安装规则不一致')
        parent = self.root / key
        generation = uuid.uuid4().hex
        temporary, final = parent / ('.tmp-' + generation), parent / generation
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            size, digest = _package_digest(Path(exe).absolute(), cancel, temporary / 'dependencies.exe')
            record = _validate_record({'schema_version': 1, 'kind': 'dependency_package', 'role': role,
                                       'source_hint': source_hint, 'files': files, 'recipe': recipe,
                                       'cache_key': key, 'package': {'path': str(final / 'dependencies.exe'),
                                                                   'size': size, 'sha256': digest}}, self.root)
            _atomic_json(temporary / 'record.json', record, cancel)
            _check_cancel(cancel)
            os.replace(temporary, final)
            # A bundle build defers activation until all its outputs are published.
            # Cancellation can leave a valid unreferenced generation, never a
            # latest pointer referring to an incomplete package.
            if activate:
                self.activate(record, cancel)
            return deepcopy(record)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def activate(self, record: dict, cancel=None) -> None:
        """Atomically activate a verified, immutable generation owned by this cache."""
        _check_cancel(cancel)
        validated = _validate_record(record, self.root)
        if validated['kind'] != 'dependency_package':
            raise ValueError('仅已生成的依赖包可以激活')
        key = validated['cache_key']
        package = Path(validated['package']['path'])
        parent = self.root / key
        generation = package.parent
        if (package.name != 'dependencies.exe' or generation.parent != parent
                or re.fullmatch(r'[0-9a-f]{32}', generation.name) is None):
            raise ValueError('依赖包不属于当前缓存的有效代目录')
        for directory in (self.root, parent, generation):
            if not stat.S_ISDIR(_safe_stat(directory).st_mode):
                raise ValueError('依赖缓存代目录无效')
        if not package.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('依赖包路径越出当前缓存')
        stored = load_record(generation / 'record.json')
        if stored != validated:
            raise ValueError('依赖缓存记录与已发布代记录不一致')
        if not _verify_package(validated, cancel):
            raise ValueError('依赖包缺失或 SHA-256 不匹配，不能激活')
        _atomic_json(parent / 'latest.json', validated, cancel)
