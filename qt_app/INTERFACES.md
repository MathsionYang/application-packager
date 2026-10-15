# 模块接口约定

所有 Python 包从 `qt_app` 目录运行，不使用相对当前工作目录的外部资源路径。源包名 `packager`、UI 包名 `ui`。

## packager.models

```python
DEFAULT_COMPILER = r'D:\PCAPP\Inno Setup 7\ISCC.exe'
ROLE_ORDER = ('C', 'A', 'B')
ROLE_NAMES = {'C':'功能软件 C', 'A':'认证软件 A', 'B':'引擎软件 B'}

@dataclass
class RuntimeInstaller:
    path: Path
    params: str = ''
    success_codes: tuple[int, ...] = (0, 3010)
    def to_dict(self) -> dict: ...  # keys path, params, success_codes
    @classmethod
    def from_dict(cls, data: dict) -> RuntimeInstaller: ...

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
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> BundleConfig: ...

@dataclass(frozen=True)
class ResolvedSoftware:
    role: str
    source_dir: Path
    entry_relative: Path
    name: str
    version: str
    @property
    def entry_path(self) -> Path: ...

@dataclass(frozen=True)
class BuildResult:
    output_file: Path
    artifact_dir: Path
    manifest_file: Path
    build_id: str
    software_roles: list[str]
    runtime_count: int
```

`BundleConfig.to_dict` 格式延续 HTML 配置：paths/output/name/version/runtimes，并增加 entries/compiler/desktop_shortcuts；`from_dict` 接受原型导出的格式。所有 Path 转为字符串。

## packager.validation

```python
class ValidationError(ValueError): pass
class EntrySelectionRequired(ValidationError):
    role: str
    source_dir: Path
    candidates: list[Path]  # absolute executable paths
def validate_config(config: BundleConfig, *, check_files: bool = True) -> list[ResolvedSoftware]: ...
def default_runtime_params(path: str | Path) -> str: ...
```

`validate_config` 检查编译器、真实输入、环境 EXE/MSI、输出冲突、源目录重叠、入口，返回 C/A/B 顺序的软件。默认完整检查发布树；GUI 和构建器使用 `check_files=False` 做轻量入口及路径校验，随后由构建器索引统一检查源树的链接、重解析点及文件类型。目录多个入口时抛 EntrySelectionRequired；GUI 选择后将相对路径写入 config.entries 再校验。源为主 EXE 时，父目录作为完整分发根。不创建输出、不执行安装器。

## packager.store

```python
class AppStore:
    def __init__(self, base_dir: Path | None = None): ...
    base_dir: Path
    def load_config(self) -> BundleConfig: ...
    def save_config(self, config: BundleConfig) -> None: ...
    def load_history(self) -> list[dict]: ...
    def add_history(self, record: dict) -> None: ...
```

默认目录 `%LOCALAPPDATA%/DesktopPackager`；产品标识文件为 `store.base_dir / 'products.json'`。历史记录包含 id/date/name/status/config/output_file/artifact_dir/logs/error；最新在前，保留最近 30 条。配置/历史损坏时安全回退，同时保留原文件而非删除。

## packager.builder

```python
class BuildCancelled(Exception): pass
def build_bundle(
    config: BundleConfig, *,
    log: Callable[[str], None] = lambda _: None,
    progress: Callable[[int, str], None] = lambda *_: None,
    cancel: threading.Event | None = None,
    profile_path: Path | None = None,
) -> BuildResult: ...
```

同步纯 Python 函数，GUI 在工作线程调用。失败抛异常，取消抛 BuildCancelled。所有真实 ISCC 输出与阶段经 log/progress 回调传递。profile_path 指向稳定产品标识文件，不将源路径或组件组合用于升级身份。入口文件夹名/EXE 名和可读取的产品元数据用于查找既有产品，三角色不同产品不可共享标识。

A/B/C 直接从所选发布目录编译，不复制完整软件载荷或计算输入全文哈希。每个软件编译前后比较元数据索引，源文件集合或元数据变化即停止发布。schema v2 清单使用 `input_mode=direct_source`、`source_integrity=metadata_before_after`；软件输入文件记录 path/size/mtime_ns/preserve_on_upgrade，运行环境及构建输出仍保留 SHA-256。编译期间要求源目录稳定；元数据一致不能保证内容字节一致。

## ui.mainwindow

```python
class MainWindow(QMainWindow):
    def __init__(self, store: AppStore | None = None): ...
```

使用原生 Qt Widgets，原型文件仅为参考，不通过 QWebEngine 嵌入。应用 `main.py` 创建 QApplication/设置 Segoe UI/中文字体，再构造 MainWindow。可提供 `--screenshot-dir` 非交互渲染截图入口，测试不应启动真实用户安装器。
