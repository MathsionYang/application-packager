from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_COMPILER = r'D:\PCAPP\Inno Setup 7\ISCC.exe'
ROLE_ORDER = ('C', 'A', 'B')
ROLE_NAMES = {'C': '功能软件 C', 'A': '认证软件 A', 'B': '引擎软件 B'}


@dataclass
class RuntimeInstaller:
    path: Path
    params: str = ''
    success_codes: tuple[int, ...] = (0, 3010)

    def __post_init__(self):
        self.path = Path(self.path)
        self.success_codes = tuple(self.success_codes)

    def to_dict(self) -> dict:
        return {'path': str(self.path), 'params': self.params, 'success_codes': list(self.success_codes)}

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict) or not isinstance(data.get('path'), str) or not data['path'].strip():
            raise ValueError('运行库路径不能为空')
        params = data.get('params', '')
        codes = data.get('success_codes', [0, 3010])
        if not isinstance(params, str) or not isinstance(codes, (tuple, list)) or not codes:
            raise ValueError('运行库参数或成功退出码格式错误')
        if any(type(code) is not int or not 0 <= code <= 2147483647 for code in codes):
            raise ValueError('运行库成功退出码必须为非负整数')
        return cls(Path(data['path']), params, tuple(codes))


@dataclass
class BundleConfig:
    paths: dict[str, str] = field(default_factory=dict)
    output: str = ''
    name: str = '桌面软件套装'
    version: str = '1.0.0'
    runtimes: list[RuntimeInstaller] = field(default_factory=list)
    entries: dict[str, str] = field(default_factory=dict)
    compiler: str = DEFAULT_COMPILER
    desktop_shortcuts: bool = True
    dependency_files: dict[str, list[str]] = field(default_factory=dict)
    dependency_records: dict[str, str] = field(default_factory=dict)
    hash_workers: int = 2
    icon_path: str = ''
    auto_version: bool = True

    def to_dict(self) -> dict:
        return {'paths': dict(self.paths), 'output': self.output, 'icon_path': self.icon_path,
                'name': self.name, 'version': self.version, 'auto_version': self.auto_version,
                'runtimes': [runtime.to_dict() for runtime in self.runtimes], 'entries': dict(self.entries),
                'compiler': self.compiler, 'desktop_shortcuts': self.desktop_shortcuts,
                'dependency_files': {role: list(paths) for role, paths in self.dependency_files.items()},
                'dependency_records': dict(self.dependency_records), 'hash_workers': self.hash_workers}

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict):
            raise ValueError('配置必须为 JSON 对象')
        defaults = cls()
        values = {}
        for key in ('output', 'icon_path', 'name', 'version', 'compiler'):
            value = data.get(key, getattr(defaults, key))
            if not isinstance(value, str):
                raise ValueError(f'配置字段 {key} 必须为文本')
            values[key] = value
        for key in ('paths', 'entries'):
            value = data.get(key, {})
            if not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values()):
                raise ValueError(f'配置字段 {key} 必须为路径对象')
            values[key] = {role: value[role] for role in ROLE_ORDER if role in value}
        runtimes = data.get('runtimes', [])
        if not isinstance(runtimes, list):
            raise ValueError('运行库列表格式错误')
        values['runtimes'] = [RuntimeInstaller.from_dict(runtime) for runtime in runtimes]
        shortcuts = data.get('desktop_shortcuts', True)
        if not isinstance(shortcuts, bool):
            raise ValueError('桌面快捷方式选项格式错误')
        values['desktop_shortcuts'] = shortcuts
        auto_version = data.get('auto_version', True)
        if not isinstance(auto_version, bool):
            raise ValueError('自动版本选项必须为布尔值')
        values['auto_version'] = auto_version
        from .dependencies import normalize_path
        selections = data.get('dependency_files', {})
        if not isinstance(selections, dict) or any(role not in ROLE_ORDER for role in selections):
            raise ValueError('依赖文件必须按 C / A / B 保存')
        values['dependency_files'] = {}
        for role, paths in selections.items():
            if not isinstance(paths, list):
                raise ValueError('依赖文件必须为相对 DLL 路径列表')
            normalized = [normalize_path(path, dll_only=True) for path in paths]
            if len({path.casefold() for path in normalized}) != len(normalized):
                raise ValueError('依赖文件路径重复')
            values['dependency_files'][role] = normalized
        records = data.get('dependency_records', {})
        if (not isinstance(records, dict) or any(role not in ROLE_ORDER for role in records)
                or any(not isinstance(path, str) or not path.strip() or any(ord(c) < 32 for c in path)
                       for path in records.values())):
            raise ValueError('依赖记录必须为 C / A / B 对应的文件路径')
        values['dependency_records'] = dict(records)
        workers = data.get('hash_workers', 2)
        if type(workers) is not int or not 1 <= workers <= 8:
            raise ValueError('哈希并发数必须为 1 到 8 的整数')
        values['hash_workers'] = workers
        return cls(**values)


@dataclass(frozen=True)
class ResolvedSoftware:
    role: str
    source_dir: Path
    entry_relative: Path
    name: str
    version: str

    @property
    def entry_path(self) -> Path:
        return self.source_dir / self.entry_relative


@dataclass(frozen=True)
class BuildResult:
    output_file: Path
    artifact_dir: Path
    manifest_file: Path
    build_id: str
    software_roles: list[str]
    runtime_count: int
    dependency_records: dict[str, str] = field(default_factory=dict)
