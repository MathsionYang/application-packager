# 启动与部署验证记录

日期：2026-10-15。

## 环境

- `py -3.13`：CPython 3.13.14（解释器文件目录名称为 `python314`，以 `sys.version` 为准）。
- `PySide6-Essentials`：6.12.0，已固定于 requirements.txt。
- `PyInstaller`：6.20.0。
- Windows PowerShell；项目路径包含中文。

## 早期启动验证记录（当前发布验证见下节）

- 先添加启动行为测试，再运行 `python -m pytest tests/test_launcher.py -q`：两个测试因缺少 `main.py` 预期失败。
- `py -3.13 -c "import sys; print(sys.version); ..."`：确认 Python、Qt 与 PyInstaller 实际版本。
- `py -3.13 -m pytest tests/test_launcher.py::test_help_can_run_from_another_directory -q`：1 passed，确认任意工作目录下可解析命令行帮助。
- `py -3.13 -m py_compile main.py tests/test_launcher.py`：通过。
- PowerShell Parser 静态解析 `build.ps1`：通过，尚未执行打包。
- 从 `C:\Users\Administrator\AppData\Local\Temp` 通过 cmd 执行中文绝对路径下的 `launch.bat --help`：退出码 0，帮助内容正确。
- `py -3.13 -m pytest tests/test_launcher.py -q`：2 passed，截图测试使用独立中文数据/截图目录，不生成 EXE。
- `py -3.13 main.py --screenshot-dir qa-artifacts/launcher-native --data-dir qa-artifacts/launcher-data`：退出码 0，生成 1360×960 原生窗口截图，视觉检查中文显示正确，Qt 控件布局正常。
- 首次真实构建发现 Windows PowerShell 5.1 的原生命令参数传递会去掉 Python `-c` 文本内的双引号；版本/依赖检查已改为 here-string 经 stdin 传入 Python。再次运行脚本验证修复。
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1`：成功构建无控制台 `dist\DesktopPackager\DesktopPackager.exe`，初次构建约 29 秒。
- 从系统 TEMP 目录通过 Python subprocess 启动冻结 EXE：`--smoke-test` 退出码 0；`--screenshot-dir` 退出码 0，原生截图为 110901 字节。两次均使用独立数据目录。
- PyInstaller warning 文件经检查：列出的 Unix/Java 平台条件依赖及 `collections.abc` 别名提示，没有缺失项目模块；冻结 EXE 的 UI/SVG 实际渲染已通过。
- `py -3.13 -m PyInstaller.utils.cliutils.archive_viewer -l -r dist/DesktopPackager/DesktopPackager.exe`：递归归档列表确认 `packager.builder`、`packager.inno`、`packager.validation`、`ui.mainwindow` 已包含。

## 前一轮发布构建（直读源目录版本）

- 2026-10-14 19:43（北京时间）：源码及 README 冻结后执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1`，使用 PyInstaller `--clean` 完整重建。日志确认 Analysis/PYZ/PKG/EXE 均因缓存不存在而重新生成，没有只更新 ZIP。
- 本版 A/B/C 从源目录直接编译，取消完整软件快照及输入 SHA-256；编译前后通过文件集合与元数据检查发现变化。轻量预检、说明文字清理、Qt 安装预览删除均已包含。
- 主任务独立执行最终完整测试集：`57 passed in 17.38s`，日志 `qa-artifacts/direct-source-tests.txt` 已读取核对；独立审查无 high/medium 问题。
- 最终冻结 EXE 从系统 TEMP 目录运行 `--smoke-test` 和 Windows 原生截图模式，退出码均为 0；截图 `qa-artifacts/release/screenshots/workbench.png` 视觉检查中文及布局正常。
- 冻结归档递归检查确认 builder、inno、validation、MainWindow、theme 模块全部包含，已删除的 `ui.installer` 不在归档；列表保存在 `qa-artifacts/release/archive-modules.txt`。工作台截图确认无安装预览按钮，中文和布局正常。
- 新 `DesktopPackager.exe` 为 1,908,912 字节，修改时间 19:43:17；SHA-256 与前一版不同，已实际重新生成。
- 已生成 `dist/DesktopPackager-Windows-x64.zip`（37,238,003 字节，约 35.5 MiB），包含完整 160 文件的 `DesktopPackager` 文件夹及根目录中文 README。ZIP CRC 检查通过；ZIP 内 EXE 与本地 EXE、ZIP 内 README 与当前源码 README 均逐字节一致。
- EXE SHA-256：`ac35b2dae38925659d3fb84845de7b108b47b34031d004c86799ee8c8eb91e7b`。
- ZIP SHA-256：`0212ad81602baeb2059c361096d1250584e3307e5fc5687abd9fed6681cc4394`。
- 源代码、EXE、ZIP 和截图校验值记录于 `qa-artifacts/release/release-manifest.json`；构建与截图后再次核对，源码未变化。
- 本轮发布源码清单按现存代码重新生成，移除了旧 `ui/installer.py` 项；清单同时记录 removed_modules、冻结检查返回码、旧 EXE 校验值和新 EXE 修改时间。
- 构建脚本已设置为无控制台 onedir，支持含空格及中文路径，不包含手动递归清理操作。
- 启动模式不自动开始打包；显式 `--smoke-test`（未同时截图）强制离屏。Windows 截图使用原生 QPA 短暂显示窗口后保存，规避离屏平台中文字体缺失问题。

未运行真实目标软件或运行库安装。后续编译成功不能作为实际安装成功的证据。

## 前一轮发布构建（安装路径、独立卸载与依赖缓存）

- DLL 人工选择、哈希记录导入/导出、并行校验和缓存复用已实现；运行库失败/取消只记录警告并继续后续安装；总安装器支持人工选择 ICO 图标。最终完整测试 **132 passed in 43.90s**。
- 总安装器提供安装根目录选择页，通过 `/DIR` 将各自的产品子目录传给所选 A/B/C 子安装器。各子安装器显式注册卸载项，并增加开始菜单卸载快捷方式。
- 真实 Inno 编译覆盖缓存辅助器、external/Hash 主软件子包及总包；两次完整夹具构建验证第二次命中缓存。另运行无业务文件的 Inno 路径事件夹具，验证超长目录拦截、可选组件及返回修改路径；Windows 原生图标解析验证含逗号路径。未执行业务软件或 VC 安装。
- 使用 Inno Setup 真实编译器额外验证 `SetupIconFile` 自定义图标总安装器脚本通过。
- 旧版程序运行中占用原输出目录，本轮使用 PyInstaller `--clean` 在 `dist/latest/DesktopPackager` 独立重建，再将新版 EXE 更新到 `dist/DesktopPackager/DesktopPackager.exe`。冻结离屏冒烟及 Windows 原生截图均退出 0，8 个核心/UI 模块的冻结代码与当前源码一致。
- EXE 为 2,453,654 字节；SHA-256 为 `30810056fd893917acc6c6eb0c7b5e63ff0b767d30f5e5c54c0637ff14240985`。
- ZIP 为 38,267,726 字节；SHA-256 为 `6a37221e25f1e10f3900cb3678d94814d1c139e1a9b29cceb872d26ac141b4b0`。CRC、内含 EXE/README 一致性通过。
- 当前发布清单记录源码、测试及文档校验值；详细操作、验证证据与边界见 `DLL依赖缓存-实现与验证.md`。

## 当前发布构建（自动版本与离线 HTML 用户手册）

- 套装版本默认跟随 C 的主 EXE：优先合法 `ProductVersion`，其次 `FileVersion`，都不可用则使用 `1.0.0` 并记录原因。可取消自动选项改用手填版本；子软件版本各自读取主 EXE。产品 AppId 沿用既有身份表，版本变化不生成新的产品身份。
- 自动版本在轻量预检及正式构建分别读取，正式构建值同步到界面、保存配置、历史、文件名、Inno `AppVersion` 和 manifest。旧配置没有 `auto_version` 时默认开启；手册说明了如何保留手填版本。
- 新增 21 项版本相关测试，覆盖版本优先级、缺省、手动模式、配置兼容、产品身份稳定、主入口选择和构建失败后的版本同步。主任务独立完整验证：**153 passed in 42.27s**，证据为 `qa-artifacts/release/final-tests.txt`。只读代码审查无 Critical / Important 问题。
- [用户手册.html](用户手册.html) 为独立离线文件，含阅读指南、12 章操作和 5 张当前原生 Qt 截图。涵盖 A/B/C 来源、多个运行环境、DLL 记录与缓存、输出、版本、安装路径、升级、独立卸载和排错。README 和发布目录均提供手册入口。
- Edge 无头浏览器验证 1440px 桌面以及 390 / 768 / 820px 布局无横向溢出；章节搜索、无匹配结果、目录定位和当前章节高亮、图片对话框、FAQ 展开收起、快捷键和复制反馈通过。打印样式及 PDF 渲染通过；59 个 ID 唯一，内部链接有效，无 JavaScript 错误及外部网络请求。证据为 `qa-artifacts/manual/manual-qa.json` 和同目录截图。
- PyInstaller `--clean` 在 `dist/latest/DesktopPackager` 完整重建，再安全更新原发布目录。基础库 ZIP 与旧包仅归档元数据不同，逐项内容相同，保留原文件以兼容正在运行的旧进程；未终止用户程序。重新启动程序即可加载新版。
- 冻结程序从系统 TEMP 使用独立数据目录执行离屏冒烟及 Windows 原生截图，均退出 0；8 个核心/UI 模块的冻结代码与当前源码一致，已删除的安装预览模块不在归档。
- `dist/DesktopPackager/DesktopPackager.exe`：2,455,647 字节；SHA-256 `cb44e57ef83cc4cf9c64a74efc549d10c8672f84e15d9b8260229b573b98de3e`。
- `dist/DesktopPackager-Windows-x64.zip`：38,833,575 字节；SHA-256 `f43407c3a55f321b06ed5f0324b2b9b8386489fbd80ce9f8f35029c8976ed525`。包含完整 166 文件的程序目录，根目录另附 README 和 HTML 手册。CRC 校验通过，逐个文件与本地发布目录一致，README / HTML 与源码一致。
- 当前发布清单及文件校验值见 `qa-artifacts/release/release-manifest.json`。本轮没有执行真实业务软件或运行环境安装。
