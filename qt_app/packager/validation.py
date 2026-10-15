import os
import re
import stat
from pathlib import Path

from .models import BundleConfig, ResolvedSoftware, ROLE_NAMES, ROLE_ORDER


class ValidationError(ValueError):
    pass


class EntrySelectionRequired(ValidationError):
    def __init__(self, role: str, source_dir: Path, candidates: list[Path]):
        self.role = role
        self.source_dir = source_dir
        self.candidates = candidates
        super().__init__(f'{ROLE_NAMES[role]} 包含多个可执行文件，请选择主程序入口')


def default_runtime_params(path: str | Path) -> str:
    name = Path(path).name.lower()
    if name.endswith('.msi'):
        return '/qn /norestart'
    if re.fullmatch(r'vc_redist\.(x64|x86|arm64)\.exe', name):
        return '/install /quiet /norestart'
    return ''


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()


def _readable_file(path: Path, label: str):
    if not path.is_file():
        raise ValidationError(f'{label}不存在或不是文件：{path}')
    try:
        with path.open('rb') as stream:
            stream.read(1)
    except OSError as error:
        raise ValidationError(f'{label}无法读取：{path}') from error


def _check_tree(source: Path):
    try:
        if _reparse(source):
            raise ValidationError(f'软件目录不支持符号链接或重解析点：{source}')
        for root, directories, files in os.walk(source, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error)):
            for name in directories + files:
                path = Path(root) / name
                if _reparse(path):
                    raise ValidationError(f'软件目录不支持符号链接或重解析点：{path}')
                if path.is_file():
                    _readable_file(path, '软件文件')
    except OSError as error:
        raise ValidationError(f'无法读取软件发布目录：{source}；{error}') from error


def validate_config(config: BundleConfig, *, check_files: bool = True) -> list[ResolvedSoftware]:
    if not config.name.strip() or any(ord(c) < 32 for c in config.name) or len(config.name) > 120:
        raise ValidationError('安装包名称不能为空、包含控制字符或超过 120 字')
    if not config.auto_version and not re.fullmatch(r'\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?', config.version):
        raise ValidationError('版本格式错误，请使用例如 1.0.0 的版本号')
    compiler = Path(config.compiler.strip())
    _readable_file(compiler, 'Inno Setup 编译器')
    if compiler.suffix.lower() != '.exe':
        raise ValidationError('请选择 Inno Setup 的 ISCC.exe 编译器')
    if not config.paths.get('C', '').strip():
        raise ValidationError('功能软件 C 的路径必填')
    if not config.output.strip():
        raise ValidationError('安装包输出目录必填')
    output = Path(config.output.strip()).resolve()
    if output.exists() and not output.is_dir():
        raise ValidationError('安装包输出路径必须为目录')
    ancestor = output
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
        raise ValidationError('安装包输出目录不可写')

    result = []
    for role in ROLE_ORDER:
        raw = config.paths.get(role, '').strip()
        if not raw:
            continue
        entered = Path(raw)
        if not entered.exists():
            raise ValidationError(f'{ROLE_NAMES[role]}路径不存在：{entered}')
        if _reparse(entered):
            raise ValidationError(f'{ROLE_NAMES[role]}不支持符号链接或重解析点')
        is_exe = entered.is_file() and entered.suffix.lower() == '.exe'
        if not entered.is_dir() and not is_exe:
            raise ValidationError(f'{ROLE_NAMES[role]}请选择发布目录或主程序 EXE')
        source = (entered.parent if is_exe else entered).resolve()
        if _within(output, source) or _within(source, output):
            raise ValidationError('输出目录与软件发布目录不能相同或互相包含，请选择单独的输出目录')
        for previous in result:
            if _within(source, previous.source_dir) or _within(previous.source_dir, source):
                raise ValidationError('A / B / C 发布目录不能相同或互相包含')
        if check_files:
            _check_tree(source)
        chosen = config.entries.get(role, '').strip()
        if chosen:
            entry = (source / chosen).resolve()
        elif is_exe:
            entry = entered.resolve()
        else:
            candidates = sorted((p for p in source.iterdir() if p.is_file() and p.suffix.lower() == '.exe'), key=lambda p: p.name.lower())
            if not candidates:
                candidates = sorted(source.rglob('*.exe'), key=lambda p: str(p).lower())
            if len(candidates) > 1:
                raise EntrySelectionRequired(role, source, candidates)
            if not candidates:
                raise ValidationError(f'{ROLE_NAMES[role]}未找到主程序 EXE')
            entry = candidates[0].resolve()
        if not _within(entry, source) or entry.suffix.lower() != '.exe' or not entry.is_file():
            raise ValidationError(f'{ROLE_NAMES[role]}入口必须为发布目录内的 EXE 文件')
        _readable_file(entry, ROLE_NAMES[role] + '入口')
        # The role label is only a fallback. Product metadata and stable identity
        # are read by the Inno builder and persisted independently of version.
        name = entry.stem
        result.append(ResolvedSoftware(role, source, entry.relative_to(source), name, config.version))

    # The icon is compiled into the outer setup executable. Keep it outside
    # all input/output trees so a build can never accidentally package its
    # own branding asset or overwrite the selected source file.
    icon_raw = config.icon_path.strip()
    if icon_raw:
        icon = Path(icon_raw).resolve()
        if icon.suffix.casefold() != '.ico':
            raise ValidationError('安装包图标必须是 ICO（.ico）文件')
        _readable_file(icon, '安装包图标')
        if _within(icon, output):
            raise ValidationError('安装包图标不能位于输出目录内')
        if any(_within(icon, item.source_dir) for item in result):
            raise ValidationError('安装包图标不能位于软件源目录内')

    runtime_paths = set()
    for runtime in config.runtimes:
        path = runtime.path.resolve()
        _readable_file(path, '运行库安装包')
        if path.suffix.lower() not in ('.exe', '.msi'):
            raise ValidationError('运行库安装包仅支持 EXE / MSI')
        identity = str(path).casefold()
        if identity in runtime_paths:
            raise ValidationError(f'运行库安装包重复：{path.name}')
        runtime_paths.add(identity)
        if any(ord(char) < 32 for char in runtime.params):
            raise ValidationError('运行库参数不能包含换行或控制字符')
        if not runtime.success_codes or any(type(code) is not int or not 0 <= code <= 2147483647 for code in runtime.success_codes):
            raise ValidationError('运行库成功退出码必须为非负整数')
    return result
