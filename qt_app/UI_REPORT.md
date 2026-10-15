# 原生 Qt UI 当前实现与验证

## 本轮修改

- 按《说明性文本删除确认清单》处理 G/S/W/H/O 全部说明性文案，移除英文装饰标题、宣传副标题、设置页交付流程整卡、工作台摘要流程、提示脚注及对应背景、分隔和占位。页面标题使用“打包工作台”“构建记录”“工具设置”。
- B01–B10 均按建议简化：发布目录、安装参数、输出目录、ISCC.exe 路径、主程序选择、操作反馈，以及“待配置”“配置就绪”。
- 根据用户追加要求，完整删除 Qt 安装预览功能：`ui/installer.py` 已删除，主窗口与历史详情的按钮、入口、属性和导入均已移除。P01–P19 因整体界面移除而完成处理。实际生成安装器的向导由后端保留。
- 软件图标定义与 Windows 文件名工具移至 `ui/mainwindow.py`，不存在对已删除模块的依赖。
- `ValidationThread` 使用 `validate_config(self.config, check_files=False)` 进行轻量预检，仍在后台线程中执行；多入口选择与保存保持可用。构建阶段只显示实际阶段，不再固定显示“正在调用本机编译器构建”。
- 保留字段、按钮、必填/可选、真实配置、数量、路径、编译器状态、进度、错误和日志。保留固定底部操作栏及既有蓝白配色与卡片布局。
- 将历史卡的垂直尺寸策略设为 Maximum，修复从空历史切换到有记录后卡片拉高、表格上下保留大段空白的问题。

## 验证结果

- `python -m unittest discover -s tests -p test_ui.py -v`：**5/5 通过**。保留运行环境顺序/参数关联、配置往返、构建锁定、后台轻量校验响应性、成功详情解锁后复用测试；移除两项已不存在的预览特性测试。
- `python -X faulthandler -m pytest tests/test_ui.py tests/test_ui_build.py tests/test_launcher.py -q`：**8 passed in 9.00s**，包含真实 ISCC 构建到历史记录的集成验证。
- `python main.py --smoke-test --data-dir <临时目录>`：退出码 **0**。
- `rg` 检查 `ui/` 与 UI 测试：无 `InstallerPreview`、`ui.installer`、`open_preview`、`preview_dialog` 或安装预览按钮引用；已清单删除的说明文本无残留。
- 使用原生 Windows QPA 执行 `ui.qa.capture_screenshots` 并检查空/已配置工作台、输出选项、设置、空/非空历史与详情共 7 张截图。设置页只保留编译器卡，工作台摘要只保留软件及交付文件；无空说明卡或预览入口。有记录历史卡紧贴表格高度。当前截图已更新到 `qa-artifacts/screenshots/`，不修改发行物。

## 当前结构

`ui/` 使用 PySide6 Qt Widgets 与内联 SVG；无 HTML/WebEngine 和安装预览实现。实际校验与构建使用 `ValidationThread`/`BuildThread`，在 finished 处理器中 `wait()` 完成原生线程退出后再释放和解锁。取消与构建中退出逻辑保留。

截图函数 `ui.qa.capture_screenshots(output_dir)` 当前仅捕获工作台、设置和历史页面；不会构建、安装或启动任何源程序。本轮未打包，未修改 EXE、ZIP 或实际安装器源码。

源码已冻结。修改文件：`ui/mainwindow.py`、`ui/qa.py`、`tests/test_ui.py`、`UI_REPORT.md`；删除文件：`ui/installer.py`。
