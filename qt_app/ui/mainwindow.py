"""Installation package workshop, implemented exclusively with native Qt Widgets."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path, PureWindowsPath
import threading
import uuid

from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot, QTimer, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QScrollArea, QStackedWidget, QLineEdit, QPushButton, QCheckBox, QFileDialog,
    QMenu, QMessageBox, QInputDialog, QPlainTextEdit, QProgressBar, QDialog,
    QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSpinBox)

from packager.models import BundleConfig, RuntimeInstaller, DEFAULT_COMPILER, ROLE_ORDER, ROLE_NAMES
from packager.store import AppStore
from packager.validation import validate_config, EntrySelectionRequired, ValidationError, default_runtime_params
from .theme import (STYLESHEET, BLUE, box, card, label, button, icon_label, icon,
                    line, clear_layout, WorkshopCheckBox)
from .dependencies import DependencyDialog

ROLE_ICONS = {'C': ('app', '#5075e9', '#edf2ff'),
              'A': ('key', '#c49a54', '#fff6e8'),
              'B': ('cpu', '#5b9e91', '#eaf7f4')}


def basename(path):
    return PureWindowsPath(str(path)).name


class BuildThread(QThread):
    log = Signal(str)
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, bool)

    def __init__(self, config, cancel, profile_path, parent):
        super().__init__(parent)
        self.config, self.cancel, self.profile_path = config, cancel, profile_path

    @Slot()
    def run(self):
        try:
            from packager.builder import build_bundle, BuildCancelled
            result = build_bundle(self.config, log=self.log.emit, progress=self.progress.emit,
                                  cancel=self.cancel, profile_path=self.profile_path)
            self.succeeded.emit(result)
        except Exception as exc:
            from packager.builder import BuildCancelled
            self.failed.emit(str(exc) or type(exc).__name__, isinstance(exc, BuildCancelled))


class ValidationThread(QThread):
    """Resolve paths and entries without walking the complete source tree."""
    outcome = Signal(object)

    def __init__(self, config, cancel, parent):
        super().__init__(parent)
        self.config, self.cancel = config, cancel

    @Slot()
    def run(self):
        try:
            if self.cancel.is_set():
                self.outcome.emit(('cancelled', None))
            else:
                validate_config(self.config, check_files=False)
                self.outcome.emit(('cancelled' if self.cancel.is_set() else 'valid', self.config))
        except EntrySelectionRequired as exc:
            self.outcome.emit(('entry', exc))
        except Exception as exc:
            self.outcome.emit(('error', str(exc)))


class RuntimeRow(QFrame):
    def __init__(self, runtime, index, count, owner):
        super().__init__()
        self.runtime = runtime
        self.setProperty('kind', 'card')
        self.setStyleSheet('RuntimeRow { background:#fbfcfe; border:1px solid #e8edf6; border-radius:8px; }')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 12, 10, 10)
        layout.setSpacing(7)
        top = QHBoxLayout()
        top.setSpacing(9)
        top.addWidget(icon_label('file', '#8ba0c6', '#f0f4fd', 30))
        details, dl = box(True, spacing=4)
        dl.addWidget(label(basename(runtime.path), 'field'))
        path_label = label(str(runtime.path), 'tiny')
        path_label.setToolTip(str(runtime.path))
        path_label.setMinimumWidth(0)
        path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        dl.addWidget(path_label)
        top.addWidget(details, 1)
        top.addWidget(label(Path(runtime.path).suffix.lstrip('.').upper(), 'tag-gray'))
        for glyph, description, callback, enabled in (
            ('up', '上移运行环境', lambda: owner.move_runtime(index, -1), index > 0),
            ('down', '下移运行环境', lambda: owner.move_runtime(index, 1), index < count - 1),
            ('x', '移除运行环境', lambda: owner.remove_runtime(index), True)):
            control = button('', glyph, 'icon', callback)
            control.setFixedSize(25, 25)
            control.setEnabled(enabled)
            control.setToolTip(description)
            control.setAccessibleName(description)
            top.addWidget(control)
        layout.addLayout(top)
        self.expander = button('安装参数  ▾', kind='text')
        self.expander.setStyleSheet('padding:2px 0;text-align:left;font-size:10px;color:#98a5bb;border:0;')
        self.expander.setCheckable(True)
        self.expander.setChecked(False)
        layout.addWidget(self.expander)
        self.params = QLineEdit(runtime.params)
        self.params.setPlaceholderText('安装参数')
        self.params.setAccessibleName(f'{basename(runtime.path)} 安装参数')
        self.params.setVisible(False)
        self.expander.toggled.connect(self.params.setVisible)
        self.params.textChanged.connect(lambda value: self._params_changed(value, owner))
        layout.addWidget(self.params)

    def _params_changed(self, value, owner):
        self.runtime.params = value
        owner.persist()


class MainWindow(QMainWindow):
    def __init__(self, store: AppStore | None = None):
        super().__init__()
        self.store = store or AppStore()
        self.setWindowTitle('安装包工坊 · Desktop Packager')
        self.setWindowIcon(icon('box', BLUE, 32))
        self.resize(1360, 960)
        self.setMinimumSize(1080, 760)
        self.setStyleSheet(STYLESHEET)
        self.building = False
        self.pending_close = False
        self.loading = True
        self.runtimes = []
        self.runtime_rows = []
        self.entries = {}
        self.dependency_files = {}
        self.dependency_records = {}
        self.dependency_dialogs = {}
        self.dependency_close_pending = False
        self.compiler = DEFAULT_COMPILER
        self.build_logs = []
        self.thread = None
        self.worker = None
        self.detail_dialog = None
        self.pending_record = None
        self.history = self.store.load_history()
        self._construct()
        self.apply_config(self.store.load_config())
        self.loading = False
        self.render_history()

    def _construct(self):
        root, rl = box(False, spacing=0)
        self.setCentralWidget(root)
        sidebar, sl = box(True, (17, 29, 17, 18), 0)
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(208)
        brand, bl = box(False, (8, 0, 0, 0), 11)
        brand_icon = icon_label('box', 'white', BLUE, 38)
        bl.addWidget(brand_icon)
        brand_text, bt = box(True, spacing=5)
        title = label('安装包工坊')
        title.setStyleSheet('font-size:18px;font-weight:650;color:#202b42;')
        bt.addWidget(title)
        small = label('DESKTOP PACKAGER', 'tiny')
        small.setStyleSheet('font-size:8px;color:#9aa3b2;letter-spacing:1px;')
        bt.addWidget(small)
        bl.addWidget(brand_text, 1)
        sl.addWidget(brand)
        sl.addSpacing(44)
        nav_label = label('工作空间', 'tiny')
        nav_label.setContentsMargins(14, 0, 0, 15)
        sl.addWidget(nav_label)
        self.nav_buttons = []
        for index, (name, glyph) in enumerate((('打包工作台', 'grid'), ('构建记录', 'clock'), ('工具设置', 'settings'))):
            nav = button(name, glyph, 'nav', lambda checked=False, n=index: self.navigate(n))
            nav.setCheckable(True)
            nav.setAutoExclusive(True)
            nav.setIconSize(QSize(18, 18))
            sl.addWidget(nav)
            sl.addSpacing(7)
            self.nav_buttons.append(nav)
        self.nav_buttons[0].setChecked(True)
        sl.addStretch()
        sl.addWidget(line())
        engine, el = box(False, (9, 16, 0, 16), 10)
        el.addWidget(icon_label('terminal', '#76849c', '#f1f4f9', 33))
        engine_text, et = box(True, spacing=5)
        et.addWidget(label('Inno Setup 7', 'field'))
        self.engine_status = label('●  检查编译器路径', 'tiny')
        et.addWidget(self.engine_status)
        el.addWidget(engine_text)
        sl.addWidget(engine)
        sl.addWidget(button('关于安装包工坊', 'info', 'text', self.show_about))
        sl.addSpacing(10)
        sl.addWidget(label('v1.0', 'tiny'))
        rl.addWidget(sidebar)
        content, cl = box(True, spacing=0)
        content.setObjectName('content')
        topbar, tl = box(False, (35, 0, 35, 0), 18)
        topbar.setObjectName('topbar')
        topbar.setFixedHeight(70)
        self.breadcrumb = label('工作空间    /    打包工作台', 'muted')
        tl.addWidget(self.breadcrumb)
        tl.addStretch()
        avatar = label('P')
        avatar.setFixedSize(29, 29)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet('background:#e9edf6;color:#7887a4;border-radius:14px;font-size:11px;font-weight:600;')
        tl.addWidget(avatar)
        cl.addWidget(topbar)
        self.pages = QStackedWidget()
        cl.addWidget(self.pages, 1)
        self._workspace()
        self._history_page()
        self._settings_page()
        rl.addWidget(content, 1)

    def _page(self):
        outer, ol = box(True, spacing=0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page, pl = box(True, (35, 30, 35, 22), 22)
        page.setObjectName('page')
        scroll.setWidget(page)
        ol.addWidget(scroll, 1)
        self.pages.addWidget(outer)
        return outer, ol, pl, scroll

    def _heading(self, layout, title, actions=()):
        heading, hl = box(False, spacing=16)
        hl.addWidget(label(title, 'heading'), 1)
        for control in actions:
            hl.addWidget(control)
        layout.addWidget(heading)

    def _section_heading(self, layout, number, title, hint='', action=None):
        row = QHBoxLayout()
        row.setSpacing(9)
        if number:
            row.addWidget(label(number, 'step'))
        row.addWidget(label(title, 'section'))
        row.addStretch()
        if hint:
            row.addWidget(label(hint, 'tiny'))
        if action:
            row.addWidget(action)
        layout.addLayout(row)

    def _path(self, placeholder, browse, clear=None):
        frame = QFrame()
        frame.setProperty('kind', 'path')
        frame.setFixedHeight(38)
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        edit = QLineEdit()
        edit.setProperty('kind', 'path')
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(0)
        row.addWidget(edit, 1)
        if clear:
            clear_button = button('', 'x', 'icon', edit.clear)
            clear_button.setFixedSize(26, 26)
            clear_button.setAccessibleName(clear)
            row.addWidget(clear_button)
        browse_button = button('浏览', 'folder', 'browse', browse)
        row.addWidget(browse_button)
        return frame, edit, browse_button

    def _workspace(self):
        self.workspace_page, outer, pl, self.workspace_scroll = self._page()
        self._heading(pl, '打包工作台',
                      (button('加载完整示例', 'layers', 'text', self.load_example),))
        workspace, wl = box(False, spacing=22)
        wl.setAlignment(Qt.AlignmentFlag.AlignTop)
        form, self.form_layout = box(True, spacing=18)
        self.form_widget = form
        software, sw = card((22, 20, 22, 20), 17)
        self._section_heading(sw, '01', '软件发布目录')
        self.path_inputs, self.path_status = {}, {}
        self.dependency_counts, self.dependency_buttons = {}, {}
        for role in ROLE_ORDER:
            if role != 'C':
                sw.addWidget(line())
            row, row_layout = box(False, spacing=13)
            glyph, color, bg = ROLE_ICONS[role]
            row_layout.addWidget(icon_label(glyph, color, bg), alignment=Qt.AlignmentFlag.AlignTop)
            fields, fl = box(True, spacing=9)
            heading = QHBoxLayout()
            heading.setSpacing(8)
            heading.addWidget(label(ROLE_NAMES[role], 'field'))
            heading.addWidget(label('必填' if role == 'C' else '可选', 'tag' if role == 'C' else 'tag-gray'))
            heading.addStretch()
            count = label('已选 0 个 DLL', 'tiny')
            self.dependency_counts[role] = count
            heading.addWidget(count)
            status = label('未配置' if role == 'C' else '未添加', 'tiny')
            self.path_status[role] = status
            heading.addWidget(status)
            fl.addLayout(heading)
            path, edit, browse = self._path('发布目录',
                                          lambda checked=False, r=role: self.browse_source(r),
                                          f'清空 {ROLE_NAMES[role]} 路径' if role != 'C' else None)
            edit.setObjectName('path' + role)
            edit.setAccessibleName(ROLE_NAMES[role] + ' 发布路径')
            edit.textChanged.connect(lambda text, r=role: self._source_changed(r))
            self.path_inputs[role] = edit
            path_row = QHBoxLayout()
            path_row.setSpacing(9)
            path_row.addWidget(path, 1)
            dependencies = button('依赖文件', 'file', 'small',
                                  lambda checked=False, r=role: self.open_dependencies(r))
            dependencies.setAccessibleName(ROLE_NAMES[role] + ' 依赖文件')
            dependencies.setFixedHeight(38)
            self.dependency_buttons[role] = dependencies
            path_row.addWidget(dependencies)
            fl.addLayout(path_row)
            row_layout.addWidget(fields, 1)
            sw.addWidget(row)
        self.form_layout.addWidget(software)
        runtime, rt = card(spacing=10)
        self.runtime_count = label('0', 'tag-gray')
        row = QHBoxLayout()
        row.setSpacing(9)
        row.addWidget(label('02', 'step'))
        row.addWidget(label('运行环境安装包', 'section'))
        row.addWidget(self.runtime_count)
        row.addStretch()
        self.add_runtime_button = button('添加安装包', 'plus', 'small', self.browse_runtimes)
        row.addWidget(self.add_runtime_button)
        rt.addLayout(row)
        self.runtime_container, self.runtime_layout = box(True, spacing=9)
        rt.addWidget(self.runtime_container)
        self.runtime_empty, rel = box(False, (0, 4, 0, 4), 0)
        rel.addStretch()
        rel.addWidget(button('添加环境', 'plus', 'link', self.browse_runtimes))
        rel.addStretch()
        rt.addWidget(self.runtime_empty)
        self.form_layout.addWidget(runtime)
        output, out = card(spacing=12)
        self._section_heading(out, '03', '安装包输出')
        out.addWidget(label('输出目录  *', 'field'))
        path, self.output_input, _ = self._path('输出目录', self.browse_output)
        self.output_input.setObjectName('outputPath')
        self.output_input.textChanged.connect(self.config_changed)
        out.addWidget(path)
        icon_heading = QHBoxLayout()
        icon_heading.addWidget(label('安装包图标（可选）', 'field'))
        icon_heading.addStretch()
        self.icon_status = label('未选择，使用默认图标', 'tiny')
        icon_heading.addWidget(self.icon_status)
        out.addLayout(icon_heading)
        icon_path, self.icon_input, _ = self._path(
            '选择 .ico 文件', self.browse_icon, '清空安装包图标')
        self.icon_input.setObjectName('iconPath')
        self.icon_input.setAccessibleName('安装包图标路径')
        self.icon_input.textChanged.connect(self.config_changed)
        out.addWidget(icon_path)
        self.advanced_button = button('更多打包选项  ▾', kind='text')
        self.advanced_button.setStyleSheet('text-align:left;padding:8px 0;color:#8b99b0;font-size:10px;border:0;')
        self.advanced_button.setCheckable(True)
        out.addWidget(self.advanced_button)
        self.advanced, al = box(True, spacing=10)
        fields, af = box(False, spacing=14)
        name_col, nl = box(True, spacing=7)
        nl.addWidget(label('安装包名称', 'field'))
        self.name_input = QLineEdit('桌面软件套装')
        self.name_input.setMaxLength(60)
        self.name_input.textChanged.connect(self.config_changed)
        nl.addWidget(self.name_input)
        version_col, vl = box(True, spacing=7)
        vl.addWidget(label('版本号', 'field'))
        self.version_input = QLineEdit('1.0.0')
        self.version_input.setMaxLength(24)
        self.version_input.textChanged.connect(self.config_changed)
        vl.addWidget(self.version_input)
        af.addWidget(name_col, 2)
        af.addWidget(version_col, 1)
        al.addWidget(fields)
        self.shortcuts_check = WorkshopCheckBox('创建各软件独立的桌面快捷方式')
        self.shortcuts_check.setChecked(True)
        self.shortcuts_check.toggled.connect(self.config_changed)
        al.addWidget(self.shortcuts_check)
        self.advanced.setVisible(False)
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        out.addWidget(self.advanced)
        self.form_layout.addWidget(output)
        wl.addWidget(form, 1)
        summary_col, scl = box(True, spacing=16)
        summary_col.setFixedWidth(273)
        self.summary, sm = card((20, 22, 20, 22), 14)
        title = QHBoxLayout()
        title.addWidget(label('本次打包', 'section'))
        title.addStretch()
        title.addWidget(icon_label('box', '#8ea4cd', size=23))
        sm.addLayout(title)
        included = QHBoxLayout()
        included.addWidget(label('包含的软件', 'tiny'))
        included.addStretch()
        self.included_count = label('0 / 3', 'tiny')
        included.addWidget(self.included_count)
        sm.addLayout(included)
        self.summary_software, self.summary_software_layout = box(True, spacing=14)
        sm.addWidget(self.summary_software)
        sm.addWidget(line())
        sm.addWidget(label('交付文件', 'tiny'))
        artifact, ar = box(False, (10, 11, 10, 11), 7)
        artifact.setObjectName('artifactPreview')
        artifact.setStyleSheet('QWidget#artifactPreview {background:#f7f9fd;border-radius:7px;}')
        ar.addWidget(icon_label('file', '#91a5cd', size=18))
        self.artifact_label = label('', 'muted', True)
        ar.addWidget(self.artifact_label, 1)
        sm.addWidget(artifact)
        scl.addWidget(self.summary)
        scl.addStretch()
        wl.addWidget(summary_col)
        pl.addWidget(workspace)
        self.console, cs = card((0, 0, 0, 0), 0)
        console_header, ch = box(False, (17, 14, 17, 14), 8)
        ch.addWidget(icon_label('terminal', '#8a9ab6', size=18))
        ch.addWidget(label('打包日志', 'section'))
        self.build_status = label('准备中', 'tag')
        ch.addWidget(self.build_status)
        ch.addStretch()
        self.collapse_button = button('', 'up', 'icon', self.toggle_logs)
        ch.addWidget(self.collapse_button)
        cs.addWidget(console_header)
        self.build_progress = QProgressBar()
        self.build_progress.setFixedHeight(4)
        self.build_progress.setTextVisible(False)
        cs.addWidget(self.build_progress)
        stage_row, sr = box(False, (17, 10, 17, 10), 10)
        self.build_stage = label('等待开始', 'tiny')
        self.build_percent = label('0%', 'blue')
        sr.addWidget(self.build_stage, 1)
        sr.addWidget(self.build_percent)
        cs.addWidget(stage_row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(20000)
        self.log_view.setFixedHeight(175)
        cs.addWidget(self.log_view)
        self.console.hide()
        pl.addWidget(self.console)
        pl.addStretch()
        self.action_bar, ab = box(False, (35, 15, 35, 15), 9)
        self.action_bar.setObjectName('actionBar')
        self.action_bar.setStyleSheet('QWidget#actionBar {background:#f5f7fb;border-top:1px solid #e8ecf2;}')
        ab.addWidget(icon_label('info', '#8298bd', '#edf2fb', 30))
        self.readiness_title = label('待配置', 'field')
        ab.addWidget(self.readiness_title, 1)
        self.import_button = button('导入配置', 'upload', 'text', self.import_config)
        self.export_button = button('保存配置', 'save', callback=self.export_config)
        self.cancel_button = button('取消打包', callback=self.cancel_build)
        self.cancel_button.hide()
        self.build_button = button('开始打包   →', 'box', 'primary', self.start_build)
        ab.addWidget(self.import_button)
        ab.addWidget(self.export_button)
        ab.addWidget(self.cancel_button)
        ab.addWidget(self.build_button)
        outer.addWidget(self.action_bar)

    def _history_page(self):
        _, _, pl, _ = self._page()
        self._heading(pl, '构建记录',
                      (button('新的打包', 'plus', 'primary', lambda: self.navigate(0)),))
        self.history_panel, self.history_layout = card((0, 0, 0, 0), 0)
        self.history_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        pl.addWidget(self.history_panel)
        pl.addStretch()

    def _settings_page(self):
        _, _, pl, _ = self._page()
        self._heading(pl, '工具设置')
        settings, st = card((24, 24, 24, 24), 16)
        settings.setMaximumWidth(850)
        self._section_heading(st, '', '打包引擎', 'Inno Setup 7')
        st.addWidget(label('ISCC.exe 路径', 'field'))
        control, self.compiler_input, _ = self._path('ISCC.exe 路径', self.browse_compiler)
        st.addWidget(control)
        hash_row = QHBoxLayout()
        hash_row.addWidget(label('哈希线程数', 'field'))
        hash_row.addStretch()
        self.hash_workers_input = QSpinBox()
        self.hash_workers_input.setRange(1, 8)
        self.hash_workers_input.setValue(2)
        self.hash_workers_input.setFixedWidth(96)
        self.hash_workers_input.setAccessibleName('哈希线程数')
        self.hash_workers_input.valueChanged.connect(self.config_changed)
        hash_row.addWidget(self.hash_workers_input)
        st.addLayout(hash_row)
        self.compiler_feedback = label('', 'tiny', True)
        self.compiler_feedback.hide()
        st.addWidget(self.compiler_feedback)
        st.addWidget(line())
        footer = QHBoxLayout()
        footer.addWidget(button('恢复默认路径', kind='text', callback=lambda: self.compiler_input.setText(DEFAULT_COMPILER)))
        footer.addStretch()
        footer.addWidget(button('保存设置', 'check', 'primary', self.save_settings))
        st.addLayout(footer)
        pl.addWidget(settings)
        pl.addStretch()

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.breadcrumb.setText('工作空间    /    ' + ('打包工作台', '构建记录', '工具设置')[index])
        if index == 1:
            self.render_history()

    def current_config(self):
        return BundleConfig(paths={role: edit.text().strip() for role, edit in self.path_inputs.items()},
                            output=self.output_input.text().strip(), icon_path=self.icon_input.text().strip(),
                            name=self.name_input.text().strip(),
                            version=self.version_input.text().strip(),
                            runtimes=[RuntimeInstaller.from_dict(r.to_dict()) for r in self.runtimes],
                            entries=dict(self.entries), compiler=self.compiler,
                            desktop_shortcuts=self.shortcuts_check.isChecked(),
                            dependency_files={role: list(paths) for role, paths in self.dependency_files.items()},
                            dependency_records=dict(self.dependency_records),
                            hash_workers=self.hash_workers_input.value())

    def apply_config(self, config):
        if isinstance(config, dict):
            config = BundleConfig.from_dict(config)
        was_loading = self.loading
        self.loading = True
        for role in ROLE_ORDER:
            self.path_inputs[role].setText(config.paths.get(role, ''))
        self.output_input.setText(config.output)
        self.icon_input.setText(config.icon_path)
        self.name_input.setText(config.name)
        self.version_input.setText(config.version)
        self.shortcuts_check.setChecked(config.desktop_shortcuts)
        self.runtimes = [RuntimeInstaller.from_dict(r.to_dict()) for r in config.runtimes]
        self.entries = dict(config.entries)
        self.dependency_files = {role: list(paths) for role, paths in config.dependency_files.items()}
        self.dependency_records = dict(config.dependency_records)
        self.hash_workers_input.setValue(config.hash_workers)
        self.compiler = config.compiler
        self.compiler_input.setText(config.compiler)
        self.render_runtimes()
        self.render_summary()
        self.loading = was_loading
        if not self.loading:
            self.persist()

    def _source_changed(self, role):
        if not self.loading:
            self.entries.pop(role, None)
        self.config_changed()

    def config_changed(self, *_):
        if self.loading:
            return
        self.render_summary()
        self.persist()

    def persist(self):
        if self.loading:
            return
        try:
            self.store.save_config(self.current_config())
        except OSError as exc:
            self.notify('配置保存失败：' + str(exc))

    def render_summary(self):
        clear_layout(self.summary_software_layout)
        configured = 0
        for role in ROLE_ORDER:
            path = self.path_inputs[role].text().strip()
            if path:
                configured += 1
            exists = bool(path and Path(path).exists())
            self.path_status[role].setText('已配置' if exists else '路径待校验' if path else '未配置' if role == 'C' else '未添加')
            self.path_status[role].setStyleSheet(f'color:{"#299c7d" if exists else "#a6afbf"};font-size:9px;')
            self.dependency_counts[role].setText(f'已选 {len(self.dependency_files.get(role, []))} 个 DLL')
            row, lo = box(False, spacing=9)
            glyph, color, bg = ROLE_ICONS[role]
            lo.addWidget(icon_label(glyph, color if path else '#b8c1d1', bg if path else '#f5f7fa', 28))
            text, tl = box(True, spacing=3)
            name = label(ROLE_NAMES[role], 'field')
            if not path:
                name.setStyleSheet('color:#aeb8c9;font-size:10px;font-weight:500;')
            tl.addWidget(name)
            tl.addWidget(label('已加入打包' if path else '待配置' if role == 'C' else '未添加', 'tiny'))
            lo.addWidget(text, 1)
            lo.addWidget(icon_label('check' if path else 'x', '#6caa98' if path else '#c5cddb', size=14))
            self.summary_software_layout.addWidget(row)
        self.included_count.setText(f'{configured} / 3')
        self.artifact_label.setText(f'{self.name_input.text().strip() or "桌面软件套装"}-{self.version_input.text().strip() or "1.0.0"}-Bundle.exe')
        icon_path = self.icon_input.text().strip()
        self.icon_status.setText('已选择图标' if icon_path else '未选择，使用默认图标')
        self.icon_status.setStyleSheet(f'color:{"#299c7d" if icon_path else "#a6afbf"};font-size:9px;')
        compiler_exists = Path(self.compiler).is_file()
        self.engine_status.setText('●  编译器可用' if compiler_exists else '●  编译器路径待配置')
        self.engine_status.setStyleSheet(f'color:{"#299c7d" if compiler_exists else "#b39468"};font-size:10px;')
        if not self.building:
            ready = bool(self.path_inputs['C'].text().strip() and self.output_input.text().strip())
            self.readiness_title.setText('配置就绪' if ready else '待配置')

    def browse_source(self, role):
        menu = QMenu(self)
        directory = menu.addAction(icon('folder'), '选择完整发布目录')
        executable = menu.addAction(icon('app'), '选择主程序 EXE')
        chosen = menu.exec(self.cursor().pos())
        if chosen == directory:
            path = QFileDialog.getExistingDirectory(self, f'选择{ROLE_NAMES[role]}完整发布目录', self.path_inputs[role].text())
        elif chosen == executable:
            path, _ = QFileDialog.getOpenFileName(self, f'选择{ROLE_NAMES[role]}主程序', self.path_inputs[role].text(), 'Windows 程序 (*.exe)')
        else:
            return
        if path:
            self.path_inputs[role].setText(path)

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择安装包输出目录', self.output_input.text())
        if path:
            self.output_input.setText(path)

    def browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择安装包图标（ICO）', self.icon_input.text(), '图标文件 (*.ico)')
        if path:
            self.icon_input.setText(path)

    def open_dependencies(self, role):
        if self.building:
            return
        existing = self.dependency_dialogs.get(role)
        if existing:
            existing.raise_()
            existing.activateWindow()
            return
        source = self.path_inputs[role].text().strip()
        path = Path(source).resolve() if source else None
        if path is None or not path.exists() or (path.is_file() and path.suffix.casefold() != '.exe'):
            QMessageBox.warning(self, '请选择发布路径', f'请先选择{ROLE_NAMES[role]}的发布目录或主程序 EXE。')
            return
        root = path.parent if path.is_file() else path
        dialog = DependencyDialog(role, root, self.dependency_files.get(role, []),
                                  self.dependency_records.get(role, ''),
                                  self.hash_workers_input.value(), self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dependency_dialogs[role] = dialog
        dialog.selectionApplied.connect(self._dependencies_applied)
        dialog.finished.connect(lambda result, r=role, d=dialog: self._dependency_closed(r, d))
        dialog.show()

    def _dependencies_applied(self, role, root, paths, record_path):
        source = self.path_inputs[role].text().strip()
        current = Path(source).resolve() if source else None
        current_root = current.parent if current and current.is_file() else current
        if current_root != Path(root).resolve():
            self.path_inputs[role].setText(str(Path(root).resolve()))
        self.dependency_files[role] = list(paths)
        if record_path:
            self.dependency_records[role] = record_path
        else:
            self.dependency_records.pop(role, None)
        self.config_changed()

    def _dependency_closed(self, role, dialog):
        if self.dependency_dialogs.get(role) is dialog:
            self.dependency_dialogs.pop(role, None)
        dialog.deleteLater()
        if self.dependency_close_pending and not self.dependency_dialogs:
            QTimer.singleShot(0, self.close)

    def browse_runtimes(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加运行环境安装包（可多选）', '', '环境安装包 (*.exe *.msi)')
        self.add_runtime_paths(paths)

    def add_runtime_paths(self, paths):
        for path in paths:
            if str(path).casefold() in {str(r.path).casefold() for r in self.runtimes}:
                continue
            self.runtimes.append(RuntimeInstaller(Path(path), default_runtime_params(path)))
        self.render_runtimes()
        self.config_changed()

    def move_runtime(self, index, delta):
        destination = index + delta
        if 0 <= index < len(self.runtimes) and 0 <= destination < len(self.runtimes):
            self.runtimes[index], self.runtimes[destination] = self.runtimes[destination], self.runtimes[index]
            self.render_runtimes()
            self.config_changed()

    def remove_runtime(self, index):
        if 0 <= index < len(self.runtimes):
            self.runtimes.pop(index)
            self.render_runtimes()
            self.config_changed()

    def render_runtimes(self):
        clear_layout(self.runtime_layout)
        self.runtime_rows = [RuntimeRow(r, i, len(self.runtimes), self) for i, r in enumerate(self.runtimes)]
        for row in self.runtime_rows:
            self.runtime_layout.addWidget(row)
        self.runtime_count.setText(str(len(self.runtimes)))
        self.runtime_container.setVisible(bool(self.runtimes))
        self.runtime_empty.setVisible(not self.runtimes)

    def load_example(self):
        if self.building:
            self.notify('请先等待当前构建结束')
            return
        self.apply_config(BundleConfig(paths={'C': r'D:\DesktopApps\FeatureApp', 'A': r'D:\DesktopApps\AuthApp', 'B': r'D:\DesktopApps\EngineApp'},
                        output=r'D:\InstallPackages', compiler=self.compiler,
                        runtimes=[RuntimeInstaller(Path(r'D:\Prerequisites\VC_redist.x64.exe'), '/install /quiet /norestart'),
                                  RuntimeInstaller(Path(r'D:\Prerequisites\VC_redist.x86.exe'), '/install /quiet /norestart')]))
        self.notify('已加载示例')

    def browse_compiler(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Inno Setup 编译器', self.compiler_input.text(), 'Inno Setup 编译器 (ISCC.exe);;程序 (*.exe)')
        if path:
            self.compiler_input.setText(path)

    def save_settings(self):
        self.compiler_feedback.show()
        path = Path(self.compiler_input.text().strip())
        if not path.is_file() or path.name.casefold() != 'iscc.exe':
            self.compiler_feedback.setText('请选择真实存在的 ISCC.exe 文件。')
            self.compiler_feedback.setStyleSheet('color:#d95362;font-size:11px;')
            return
        self.compiler = str(path)
        self.persist()
        self.render_summary()
        self.compiler_feedback.setText('已保存')
        self.compiler_feedback.setStyleSheet('color:#299c7d;font-size:11px;')

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入安装包工坊配置', '', 'JSON 配置 (*.json)')
        if not path:
            return
        try:
            file = Path(path)
            if file.stat().st_size > 1024 * 1024:
                raise ValueError('配置文件超过 1 MB')
            data = json.loads(file.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('paths'), dict) or not isinstance(data.get('runtimes', []), list):
                raise ValueError('配置格式不正确')
            self.apply_config(BundleConfig.from_dict(data))
            self.notify('配置已导入')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, '无法导入配置', str(exc))

    def export_config(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存安装包工坊配置', '安装包工坊-配置.json', 'JSON 配置 (*.json)')
        if not path:
            return
        try:
            data = {'format': 'desktop-packager-prototype', 'version': 1, **self.current_config().to_dict()}
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.persist()
            self.notify('配置已保存为 JSON 文件')
        except OSError as exc:
            QMessageBox.warning(self, '保存失败', str(exc))

    def start_build(self):
        if self.building or self.dependency_dialogs:
            return
        self.active_config = self.current_config()
        self.build_logs = []
        self.log_view.clear()
        self.console.show()
        self.log_view.show()
        self.build_progress.setValue(0)
        self.build_percent.setText('0%')
        self.build_status.setText('构建中')
        self.build_stage.setText('准备构建')
        self.cancel_event = threading.Event()
        self._lock_build(True)
        self.readiness_title.setText('正在校验发布目录')
        self._start_validation()
        QTimer.singleShot(0, lambda: self.workspace_scroll.ensureWidgetVisible(self.console))

    def _start_validation(self):
        self.validation_outcome = None
        self.thread = ValidationThread(self.active_config, self.cancel_event, self)
        self.worker = self.thread
        self.thread.outcome.connect(self._validation_outcome)
        self.thread.finished.connect(self._validation_finished)
        self.thread.start()

    @Slot(object)
    def _validation_outcome(self, outcome):
        self.validation_outcome = outcome

    @Slot()
    def _validation_finished(self):
        self._join_finished_thread()
        self.thread = None
        self.worker = None
        kind, value = self.validation_outcome or ('error', '校验线程未返回结果')
        if self.cancel_event.is_set() or kind == 'cancelled':
            self.build_failed('已停止配置校验', True)
            self.thread_finished()
        elif kind == 'entry':
            options = [str(Path(candidate).relative_to(value.source_dir)) for candidate in value.candidates]
            chosen, accepted = QInputDialog.getItem(self, f'选择{ROLE_NAMES[value.role]}主入口',
                '选择主程序：', options, 0, False)
            if accepted:
                self.active_config.entries[value.role] = chosen
                self.entries = dict(self.active_config.entries)
                self.persist()
                self._start_validation()
            else:
                self.build_failed('用户取消主入口选择', True)
                self.thread_finished()
        elif kind == 'error':
            self.build_failed(str(value), False)
            self.thread_finished()
            QMessageBox.warning(self, '请检查打包配置', str(value))
        else:
            self.entries = dict(self.active_config.entries)
            self.persist()
            self._begin_build()

    def _begin_build(self):
        self.thread = BuildThread(self.active_config, self.cancel_event, self.store.base_dir / 'products.json', self)
        self.worker = self.thread
        self.thread.log.connect(self.append_log)
        self.thread.progress.connect(self.on_progress)
        self.thread.succeeded.connect(self.build_succeeded)
        self.thread.failed.connect(self.build_failed)
        self.thread.finished.connect(self.thread_finished)
        self.thread.start()

    def _lock_build(self, building):
        self.building = building
        self.form_widget.setEnabled(not building)
        self.pages.widget(2).setEnabled(not building)
        self.build_button.setEnabled(not building)
        self.import_button.setEnabled(not building)
        self.cancel_button.setVisible(building)
        self.cancel_button.setEnabled(building)
        self.cancel_button.setText('取消打包')
        for edit in (*self.path_inputs.values(), self.output_input, self.icon_input,
                     self.name_input, self.version_input):
            edit.setEnabled(not building)
        self.runtime_container.setEnabled(not building)
        self.add_runtime_button.setEnabled(not building)
        self.shortcuts_check.setEnabled(not building)
        if not building:
            self.render_summary()

    @Slot(str)
    def append_log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.build_logs.append({'time': timestamp, 'message': message})
        self.log_view.appendPlainText(f'{timestamp}    {message}')

    @Slot(int, str)
    def on_progress(self, percent, stage):
        self.build_progress.setValue(percent)
        self.build_percent.setText(f'{percent}%')
        self.build_stage.setText(stage)
        self.readiness_title.setText(stage)

    def _record(self, status, result=None, error=''):
        record = {'id': result.build_id if result else uuid.uuid4().hex,
                  'date': datetime.now(timezone.utc).isoformat(),
                  'name': result.output_file.name if result else f'{self.active_config.name}-{self.active_config.version}-Bundle.exe',
                  'status': status, 'config': self.active_config.to_dict(),
                  'output_file': str(result.output_file) if result else '',
                  'artifact_dir': str(result.artifact_dir) if result else '',
                  'logs': list(self.build_logs), 'error': error}
        try:
            self.store.add_history(record)
            self.history = self.store.load_history()
        except OSError as exc:
            self.notify('构建已结束，但历史记录保存失败：' + str(exc))
        self.render_history()
        return record

    @Slot(object)
    def build_succeeded(self, result):
        self.dependency_records.update(result.dependency_records)
        self.active_config.dependency_records.update(result.dependency_records)
        self.persist()
        self.on_progress(100, '构建完成 · 已生成真实安装包')
        self.build_status.setText('构建完成')
        record = self._record('success', result)
        self.pending_record = record

    @Slot(str, bool)
    def build_failed(self, error, cancelled):
        self.append_log(('构建已取消：' if cancelled else '构建失败：') + error)
        self.build_status.setText('已取消' if cancelled else '构建失败')
        self.build_stage.setText('构建已取消' if cancelled else '请查看日志并修复配置')
        self._record('cancelled' if cancelled else 'failed', error=error)
        self.notify('构建已取消' if cancelled else '构建失败，请查看日志')

    @Slot()
    def thread_finished(self):
        self._join_finished_thread()
        self.worker = None
        self.thread = None
        self._lock_build(False)
        if self.pending_close:
            self.close()
        elif self.pending_record is not None:
            record, self.pending_record = self.pending_record, None
            self.show_record(record)

    def _join_finished_thread(self):
        # Qt emits finished before native TLS cleanup returns. Joining here is
        # immediate except for that final cleanup and makes destruction safe.
        if self.thread is not None:
            self.thread.wait()
            self.thread.deleteLater()

    def cancel_build(self):
        if self.building:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText('正在取消…')
            self.readiness_title.setText('正在安全停止编译进程')

    def toggle_logs(self):
        self.log_view.setVisible(not self.log_view.isVisible())
        self.collapse_button.setIcon(icon('up' if self.log_view.isVisible() else 'down'))

    def render_history(self):
        clear_layout(self.history_layout)
        self.nav_buttons[1].setText(f'构建记录                 {len(self.history)}')
        if not self.history:
            empty, el = box(True, (20, 75, 20, 75), 14)
            el.addWidget(icon_label('clock', '#a9b9d9', size=45), alignment=Qt.AlignmentFlag.AlignHCenter)
            heading = label('还没有构建记录', 'section')
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(heading)
            el.addWidget(button('前往工作台  →', callback=lambda: self.navigate(0)), alignment=Qt.AlignmentFlag.AlignHCenter)
            self.history_layout.addWidget(empty)
            return
        table = QTableWidget(len(self.history), 4)
        table.setHorizontalHeaderLabels(['安装包', '包含内容', '状态', '操作'])
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setStyleSheet('QTableWidget{border:0;background:white;} QHeaderView::section{background:white;color:#9aa7bc;border:0;border-bottom:1px solid #e8ecf2;padding:14px;text-align:left;font-size:10px;} QTableWidget::item{border-bottom:1px solid #f1f4f8;padding:10px;color:#64748e;font-size:11px;}')
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 190), (2, 120), (3, 120)):
            table.setColumnWidth(col, width)
        for index, record in enumerate(self.history):
            date = str(record.get('date', ''))
            try:
                date = datetime.fromisoformat(date.replace('Z', '+00:00')).astimezone().strftime('%Y/%m/%d %H:%M:%S')
            except ValueError:
                pass
            table.setItem(index, 0, QTableWidgetItem(str(record.get('name', '')) + '\n' + date))
            config = record.get('config', {})
            roles = ' + '.join(r for r in ROLE_ORDER if config.get('paths', {}).get(r))
            table.setItem(index, 1, QTableWidgetItem(roles + f'\n{len(config.get("runtimes", []))} 个运行环境'))
            status = {'success': '构建完成', 'failed': '构建失败', 'cancelled': '已取消'}.get(record.get('status'), str(record.get('status', '未知')))
            table.setItem(index, 2, QTableWidgetItem(status))
            control = button('查看详情', kind='text', callback=lambda checked=False, r=record: self.show_record(r))
            table.setCellWidget(index, 3, control)
            table.setRowHeight(index, 74)
        table.setFixedHeight(50 + min(8, len(self.history)) * 74)
        self.history_layout.addWidget(table)

    def show_record(self, record):
        dialog = QDialog(self)
        dialog.setWindowTitle('构建记录 · 安装包工坊')
        dialog.resize(680, 600)
        dialog.setStyleSheet(STYLESHEET)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        status = {'success': '打包完成', 'failed': '打包失败', 'cancelled': '打包已取消'}.get(record.get('status'), '构建记录')
        heading = label(status)
        heading.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(heading)
        config = BundleConfig.from_dict(record.get('config', {}))
        for name, value in (('安装包名称', record.get('name', '')), ('输出文件', record.get('output_file') or '未生成可交付文件'),
                            ('业务软件', '、'.join(ROLE_NAMES[r] for r in ROLE_ORDER if config.paths.get(r))),
                            ('环境安装包', '、'.join(basename(r.path) for r in config.runtimes) or '无')):
            row, lo = box(False, (0, 5, 0, 5), 15)
            key = label(name, 'tiny')
            key.setFixedWidth(85)
            lo.addWidget(key, alignment=Qt.AlignmentFlag.AlignTop)
            lo.addWidget(label(str(value), 'muted', True), 1)
            layout.addWidget(row)
        if record.get('error'):
            layout.addWidget(label(str(record['error']), 'error', True))
        logs = QPlainTextEdit()
        logs.setReadOnly(True)
        messages = []
        for item in record.get('logs', []):
            messages.append((str(item.get('time', '')) + '  ' + str(item.get('message', ''))) if isinstance(item, dict) else str(item))
        logs.setPlainText('\n'.join(messages))
        layout.addWidget(logs, 1)
        actions = QHBoxLayout()
        reuse = button('使用此配置', kind='text', callback=lambda: self.reuse_record(config, dialog))
        reuse.setEnabled(not self.building)
        actions.addWidget(reuse)
        if record.get('output_file') and Path(record['output_file']).is_file():
            actions.addWidget(button('打开输出目录', 'folder', callback=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(record['output_file']).parent)))))
        actions.addStretch()
        actions.addWidget(button('关闭', callback=dialog.accept))
        layout.addLayout(actions)
        self.detail_dialog = dialog
        dialog.setModal(True)
        dialog.show()

    def reuse_record(self, config, dialog):
        if self.building:
            return
        self.apply_config(config)
        dialog.accept()
        self.navigate(0)
        self.notify('已恢复此构建配置')

    def notify(self, message):
        if not hasattr(self, 'toast'):
            self.toast = label('', wrap=True)
            self.toast.setParent(self)
            self.toast.setStyleSheet('background:#263852;color:white;border-radius:9px;padding:13px 20px;font-size:11px;')
            self.toast_timer = QTimer(self)
            self.toast_timer.setSingleShot(True)
            self.toast_timer.timeout.connect(self.toast.hide)
        self.toast.setText(message)
        self.toast.setMaximumWidth(660)
        self.toast.adjustSize()
        self.toast.move(max(220, (self.width() - self.toast.width() + 208) // 2), self.height() - self.toast.height() - 80)
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start(4500)

    def show_about(self):
        QMessageBox.information(self, '关于安装包工坊',
            '安装包工坊 · Desktop Packager 1.0')

    def closeEvent(self, event):
        if self.dependency_dialogs:
            self.dependency_close_pending = True
            for dialog in tuple(self.dependency_dialogs.values()):
                dialog.reject()
            event.ignore()
            return
        if self.building:
            if not self.pending_close:
                reply = QMessageBox.question(self, '正在构建', '取消当前构建并在安全停止后退出？',
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self.pending_close = True
                    self.cancel_build()
            event.ignore()
            return
        self.persist()
        event.accept()
