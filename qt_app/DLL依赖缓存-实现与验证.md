# DLL 依赖缓存：实现与验证

日期：2026-10-15。已按《DLL选择与哈希缓存依赖包-需求分析》实现并重新生成桌面程序。

## 使用

1. 选择软件发布目录，点击该行的“依赖文件”。列表显示全部文件，仅 DLL 可以勾选。
2. 使用搜索、类型/大小筛选、仅已选和批量勾选确定依赖集合，点击“使用选择”。A/B/C 各自保存选择。
3. “保存记录”计算所选 DLL 的 SHA-256，输出 JSON；下次可用“导入记录”恢复选择。
4. 开始打包后重新计算所选 DLL 的 SHA-256。内容、路径和规则一致且缓存包校验成功时复用；否则重建对应依赖包。
5. 成功构建后，再打开依赖窗口保存记录，可同时导出 JSON 和同名 `.files` 文件夹，里面包含依赖 EXE；迁移时一起复制。未构建或内容已变化时保存为选择记录。

环境包安装失败或在其自身窗口中取消只记录警告，不阻断后续环境包和 A/B/C 安装；C/A/B 软件安装失败仍停止后续软件。输出设置支持人工选择 `.ico`，写入总安装器的 `SetupIconFile`。

设置页的哈希线程数为 1–8，默认 2。源目录、输出目录、记录位置与缓存位置分开；软件无需先复制到临时构建目录。

## 实现

- `packager/dependencies.py`：元数据扫描、标准文件 SHA-256 的有界线程池、严格记录格式、包校验、可移植导出与不可变缓存代。
- `ui/dependencies.py`：原生 Qt 虚拟文件表、后台扫描/保存、筛选与批量选择、缺失项及异步取消。
- `packager/builder.py`：本次完整 DLL 校验、缓存复用/重建、构建证据、发布完成后的缓存激活。
- `packager/inno.py`：缓存解压辅助包、主软件 external/Hash 条目、临时盘空间检查、LZMA2 并行压缩和总包免重复压缩。
- `models.py`、`mainwindow.py`：选择/记录/线程设置持久化，以及成功记录回写。

依赖 EXE 只向主安装器临时目录解压；最终 DLL 由主软件 `[Files]` 校验并安装，沿用原 AppId、安装目录与卸载日志。原有升级保留策略不因分组改变。VC 等已有运行环境安装器仍由总包优先执行。

缓存键包含完整相对路径、文件大小、SHA、产品身份、架构、安装/压缩规则和实际 Inno 工具链指纹，不包含绝对源路径或修改时间。新增未勾选 DLL 留在主包；选中缺失项不会静默删除。准备好的缓存代在最终 EXE 发布成功前不激活，后期取消或失败不会替换旧基线。

## 验证证据

最终完整测试：`python -m pytest -q` → **128 passed in 34.41s**。

覆盖：

- 同大小、同 mtime 的内容变化仍触发重建；仅未选文件变化继续复用。
- 所选 DLL 缺失、缓存损坏、工具链变化、大小写改名、改选择与清空选择。
- 编译失败、发布后期取消，以及临时候选包变化不会激活错误基线。
- 记录导出/导入及伴随包迁移，选择记录与成功包记录严格区分。
- Qt 筛选后保留隐藏选择、真实路径恢复、保存、切根隔离旧结果和关闭时取消线程。
- 使用本机真实 Inno 7 编译 helper → 主子包 → 总包；实际执行两次完整夹具构建，第二次复用依赖包。错误 DLL 哈希与被替换缓存 EXE 均被编译器拒绝。
- Optional runtime installer failures, user cancellation, MSI 1602/1641, and other non-whitelisted exit codes are logged and skipped so later runtime and A/B/C installers continue; A/B/C failures still stop subsequent software.

原生截图已检查：`qa-artifacts/dependency-cache/workspace.png`、`dependencies.png`。均使用微型模拟目录，未扫描或哈希用户的大型业务软件目录。

29,000 条纯内存模拟文件记录、每批 500 条：插入约 0.108 秒，搜索与 DLL 筛选约 0.196 秒；自动排序约 4.042 秒，已移除自动排序。这是 Qt 列表测试，不是磁盘扫描、哈希或实际压缩速度测量。

## 发布

- `dist/DesktopPackager/DesktopPackager.exe`：已通过 PyInstaller `--clean` 完整重建，2,452,409 字节。
- `dist/DesktopPackager-Windows-x64.zip`：38,266,365 字节，含完整应用目录 164 个文件及 README。
- 冻结 EXE 从系统临时目录运行离屏冒烟与原生截图，退出码均为 0。
- 冻结归档确认包含新依赖核心和 UI 模块；不含已删除的安装预览模块。
- ZIP CRC、ZIP 内 EXE 与本地 EXE、ZIP 内 README 与源码均校验通过。
- EXE SHA-256：`539030dd71142826b0d852deedb5fddcdcb320aa9f563abe762d76326a100e12`。
- ZIP SHA-256：`ce734c330c18b2f15703b053cc13a4b471c356dd943bd6995d33b351b250f0af`。

发布清单：`qa-artifacts/release/release-manifest.json`。构建日志：`qa-artifacts/dependency-cache/build.log`。验证摘要：`qa-artifacts/runtime-icon/verification.json`。

首次仍需哈希并压缩所选 DLL；命中后仍读取当前 DLL 和缓存包做校验，并重新生成主包与总包。任一选中成员变化，首版重建该软件的整个依赖子包。未运行目标软件或运行库安装器；真实产品的安装、运行、升级与卸载仍需在测试机验收。
