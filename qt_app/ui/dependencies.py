"""Virtual dependency file list and cancellable background record operations."""
from __future__ import annotations
import copy
from pathlib import Path
import threading

from PySide6.QtCore import (Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
                            QThread, Signal, QTimer)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableView,
    QAbstractItemView, QHeaderView, QLineEdit, QComboBox, QFileDialog,
    QMessageBox, QProgressBar)
from packager.models import ROLE_NAMES
from .theme import STYLESHEET, label, button, WorkshopCheckBox


# Lazy wrappers let the app open without initializing filesystem operations.
def scan_files(*args, **kwargs):
    from packager.dependencies import scan_files as implementation
    return implementation(*args, **kwargs)


def hash_selected(*args, **kwargs):
    from packager.dependencies import hash_selected as implementation
    return implementation(*args, **kwargs)


def load_record(*args, **kwargs):
    from packager.dependencies import load_record as implementation
    return implementation(*args, **kwargs)


def save_record(*args, **kwargs):
    from packager.dependencies import save_record as implementation
    return implementation(*args, **kwargs)


def cache_key(*args, **kwargs):
    from packager.dependencies import cache_key as implementation
    return implementation(*args, **kwargs)


def relative_path(path):
    return str(path).replace('\\', '/')


def path_key(path):
    return relative_path(path).casefold()


def format_size(size):
    if size is None:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{size} B'
        size /= 1024


class DependencyFileModel(QAbstractTableModel):
    selectionChanged = Signal()

    def __init__(self, selected=(), parent=None):
        super().__init__(parent)
        self.rows = []
        self._indices = {}
        self._selected = {}
        self.scan_complete = False
        self.set_selection(selected)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ('相对路径', '大小', '状态')[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        item = self.rows[index.row()]
        key = path_key(item['path'])
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return item['path']
            if index.column() == 1:
                return format_size(item.get('size'))
            if not item['present']:
                return '缺失' if self.scan_complete else '待定位'
            return item.get('state', '待校验') if key in self._selected else ''
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0 and self.is_dll(item['path']):
            return Qt.CheckState.Checked if key in self._selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return item.get('size', -1) if index.column() == 1 else self.data(index)
        if role == Qt.ItemDataRole.ToolTipRole:
            return item['path']
        if role == Qt.ItemDataRole.ForegroundRole and not item['present'] and self.scan_complete:
            return QColor('#d95362')
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0 and self.is_dll(self.rows[index.row()]['path']):
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid() or index.column() != 0:
            return False
        item = self.rows[index.row()]
        checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        if not self.is_dll(item['path']) or (checked and not item['present']):
            return False
        self.set_checked([item['path']], checked)
        return True

    @staticmethod
    def is_dll(path):
        return str(path).casefold().endswith('.dll')

    def row_for_path(self, path):
        return self._indices[path_key(path)]

    def selected_paths(self):
        return sorted(self._selected.values(), key=str.casefold)

    def missing_paths(self):
        return [p for p in self.selected_paths() if not self.rows[self.row_for_path(p)]['present']]

    def set_selection(self, paths, record_files=()):
        self._selected = {path_key(p): relative_path(p) for p in paths if self.is_dll(p)}
        metadata = {path_key(item['path']): item for item in record_files}
        absent = []
        for key, path in self._selected.items():
            if key not in self._indices:
                absent.append({**metadata.get(key, {}), 'path': path, 'present': False})
            else:
                self._selected[key] = self.rows[self._indices[key]]['path']
        self._insert(absent)
        for row in self.rows:
            row.pop('state', None)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 2))
        self.selectionChanged.emit()

    def _insert(self, items):
        if not items:
            return
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(items) - 1)
        for item in items:
            self._indices[path_key(item['path'])] = len(self.rows)
            self.rows.append(item)
        self.endInsertRows()

    def append_batch(self, batch):
        additions = []
        seen = set(self._indices)
        changed = []
        for metadata in batch:
            item = {**metadata, 'path': relative_path(metadata['path']), 'present': True}
            key = path_key(item['path'])
            if key in self._selected:
                self._selected[key] = item['path']
            if key in self._indices:
                row = self._indices[key]
                self.rows[row] = item
                changed.append(row)
            elif key not in seen:
                additions.append(item)
                seen.add(key)
        self._insert(additions)
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), 2))

    def finish_scan(self):
        self.scan_complete = True
        if self.rows:
            self.dataChanged.emit(self.index(0, 2), self.index(len(self.rows) - 1, 2))

    def set_checked(self, paths, checked):
        changed = []
        for path in paths:
            key = path_key(path)
            row = self._indices.get(key)
            if row is None or not self.is_dll(path):
                continue
            item = self.rows[row]
            if checked and item['present'] and key not in self._selected:
                self._selected[key] = item['path']
                changed.append(row)
            elif not checked and key in self._selected:
                del self._selected[key]
                changed.append(row)
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), 2))
            self.selectionChanged.emit()

    def mark_hashed(self, files):
        for item in files:
            row = self._indices.get(path_key(item['path']))
            if row is not None:
                self.rows[row]['state'] = '已校验'
        if self.rows:
            self.dataChanged.emit(self.index(0, 2), self.index(len(self.rows) - 1, 2))


class DependencyFilterModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search = ''
        self.dll_only = False
        self.selected_only = False
        self.minimum_size = 0
        self.maximum_size = None
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filters(self, search='', dll_only=False, selected_only=False,
                    minimum_size=0, maximum_size=None):
        self.search = search.casefold().replace('\\', '/')
        self.dll_only, self.selected_only = dll_only, selected_only
        self.minimum_size, self.maximum_size = minimum_size, maximum_size
        self.invalidate()

    def filterAcceptsRow(self, source_row, parent):
        source = self.sourceModel()
        item = source.rows[source_row]
        path = item['path']
        if self.search and self.search not in path.casefold():
            return False
        if self.dll_only and not source.is_dll(path):
            return False
        if self.selected_only and path_key(path) not in source._selected:
            return False
        size = item.get('size') or 0
        return size >= self.minimum_size and (self.maximum_size is None or size < self.maximum_size)

    def visible_paths(self):
        return [self.sourceModel().rows[self.mapToSource(self.index(row, 0)).row()]['path']
                for row in range(self.rowCount())]


class DependencyTask(QThread):
    batch = Signal(int, object)
    progress = Signal(int, object)
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, kind, token, root, role, cancel, parent, **options):
        super().__init__(parent)
        self.kind, self.token, self.root, self.role = kind, token, root, role
        self.cancel, self.options = cancel, options

    def run(self):
        try:
            if self.kind == 'scan':
                result = scan_files(self.root, self.cancel,
                    on_batch=lambda items: self.batch.emit(self.token, items))
                result = {'files': result}
            elif self.kind == 'import':
                result = {'record': load_record(self.options['path'], expected_role=self.role),
                          'path': str(self.options['path'])}
            else:
                files = hash_selected(self.root, self.options['paths'], self.cancel,
                    workers=self.options['workers'],
                    progress=lambda *value: self.progress.emit(self.token, value))
                if self.cancel.is_set():
                    return
                old = self.options.get('record')
                portable = bool(old and old.get('kind') == 'dependency_package' and old.get('package')
                    and cache_key(files, old['recipe']) == old['cache_key']
                    and Path(old['package']['path']).is_file())
                if portable:
                    record = copy.deepcopy(old)
                    record['source_hint'] = str(self.root)
                    record['files'] = files
                else:
                    record = {'schema_version': 1, 'kind': 'selection', 'role': self.role,
                        'source_hint': str(self.root), 'files': files,
                        'recipe': None, 'cache_key': None, 'package': None}
                save_record(self.options['path'], record, portable=portable, cancel=self.cancel)
                if self.cancel.is_set():
                    return
                result = {'record': load_record(self.options['path'], expected_role=self.role),
                          'path': str(self.options['path']), 'portable': portable}
            if not self.cancel.is_set():
                self.completed.emit(self.token, result)
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(self.token, str(exc) or type(exc).__name__)


class DependencyDialog(QDialog):
    selectionApplied = Signal(str, str, object, str)

    def __init__(self, role, root, selected=(), record_path='', workers=2, parent=None):
        super().__init__(parent)
        self.role, self.root, self.workers = role, Path(root).resolve(), workers
        self.record_path = str(record_path)
        self.record = None
        self._token = 0
        self._task = None
        self._pending = None
        self._closing = False
        self._result = None
        self._failure = None
        self._received_batches = False
        self.setWindowTitle(f'{ROLE_NAMES[role]} · 依赖文件')
        self.resize(1000, 740)
        self.setMinimumSize(780, 580)
        self.setStyleSheet(STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 24, 25, 22)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(label(f'{ROLE_NAMES[role]} · 依赖文件', 'heading'), 1)
        self.import_button = button('导入记录', 'upload', callback=self.choose_record)
        self.save_button = button('保存记录', 'save', callback=self.choose_save)
        top.addWidget(self.import_button)
        top.addWidget(self.save_button)
        layout.addLayout(top)
        source = QHBoxLayout()
        source.addWidget(label('源目录', 'field'))
        self.root_input = QLineEdit(str(self.root))
        self.root_input.setReadOnly(True)
        source.addWidget(self.root_input, 1)
        self.root_button = button('更换目录', 'folder', 'small', self.choose_root)
        source.addWidget(self.root_button)
        layout.addLayout(source)
        filters = QHBoxLayout()
        filters.setSpacing(14)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索相对路径')
        filters.addWidget(self.search, 1)
        self.dll_filter = WorkshopCheckBox('仅 DLL')
        self.selected_filter = WorkshopCheckBox('仅已选')
        filters.addWidget(self.dll_filter)
        filters.addWidget(self.selected_filter)
        self.size_filter = QComboBox()
        for text, bounds in (('全部大小', (0, None)), ('小于 1 MB', (0, 1024**2)),
                             ('1–10 MB', (1024**2, 10 * 1024**2)), ('不小于 10 MB', (10 * 1024**2, None))):
            self.size_filter.addItem(text, bounds)
        filters.addWidget(self.size_filter)
        layout.addLayout(filters)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self.update_filters)
        self.dll_filter.toggled.connect(self.update_filters)
        self.selected_filter.toggled.connect(self.update_filters)
        self.size_filter.currentIndexChanged.connect(self.update_filters)
        self.model = DependencyFileModel(selected, self)
        self.proxy = DependencyFilterModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 110)
        self.table.setStyleSheet('QTableView {background:white;alternate-background-color:#fafbfe;border:1px solid #e8ecf2;border-radius:8px;selection-background-color:#edf2ff;selection-color:#3965ee;} QHeaderView::section {background:#f8faff;color:#8291aa;border:0;border-bottom:1px solid #e8ecf2;padding:10px;font-size:11px;}')
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.select_rows_button = button('勾选所选行', kind='small', callback=lambda: self.check_highlighted(True))
        self.clear_rows_button = button('取消所选行', kind='small', callback=lambda: self.check_highlighted(False))
        self.select_filtered_button = button('全选筛选结果', kind='small', callback=self.check_filtered)
        actions.addWidget(self.select_rows_button)
        actions.addWidget(self.clear_rows_button)
        actions.addWidget(self.select_filtered_button)
        actions.addStretch()
        self.selection_label = label('', 'field')
        actions.addWidget(self.selection_label)
        layout.addLayout(actions)
        record = QHBoxLayout()
        record.addWidget(label('记录文件', 'tiny'))
        self.record_input = QLineEdit(self.record_path)
        self.record_input.setPlaceholderText('未导入')
        self.record_input.setReadOnly(True)
        record.addWidget(self.record_input, 1)
        layout.addLayout(record)
        package = QHBoxLayout()
        package.addWidget(label('依赖包', 'tiny'))
        self.package_input = QLineEdit()
        self.package_input.setPlaceholderText('未生成')
        self.package_input.setReadOnly(True)
        package.addWidget(self.package_input, 1)
        layout.addLayout(package)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        self.status = label('等待扫描', 'muted')
        footer.addWidget(self.status, 1)
        self.cancel_button = button('取消任务', callback=self.cancel_task)
        footer.addWidget(self.cancel_button)
        self.close_button = button('关闭', callback=self.reject)
        footer.addWidget(self.close_button)
        self.apply_button = button('使用选择', 'check', 'primary', self.apply_selection)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)
        self.model.selectionChanged.connect(self.update_selection)
        self.update_selection()
        self._start_task('scan')

    @property
    def busy(self):
        return self._task is not None

    def _start_task(self, kind, **options):
        if self.busy:
            self._pending = (kind, options)
            self._token += 1
            self._task.cancel.set()
            return
        self._token += 1
        self._result, self._failure = None, None
        self._received_batches = False
        task = DependencyTask(kind, self._token, self.root, self.role, threading.Event(), self, **options)
        self._task = task
        task.batch.connect(self._batch)
        task.progress.connect(self._progress)
        task.completed.connect(self._completed)
        task.failed.connect(self._failed)
        task.finished.connect(lambda: self._retire_task(task))
        self.progress.setRange(0, 0 if kind in ('scan', 'import') else 1000)
        self.status.setText({'scan': '正在扫描', 'import': '正在导入', 'save': '正在校验所选 DLL'}[kind])
        self.update_controls()
        task.start()

    def _batch(self, token, batch):
        if token != self._token or self._closing:
            return
        self._received_batches = True
        self.model.append_batch(batch)
        self.status.setText(f'已扫描 {sum(item["present"] for item in self.model.rows):,} 个文件')

    def _progress(self, token, progress):
        if token != self._token or self._closing:
            return
        done, total, done_files, total_files = progress
        self.progress.setRange(0, 1000)
        self.progress.setValue(round(1000 * done / max(total, 1)))
        self.status.setText(f'{format_size(done)} / {format_size(total)} · {done_files} / {total_files} 个 DLL')

    def _completed(self, token, result):
        if token == self._token and not self._closing:
            self._result = result

    def _failed(self, token, error):
        if token == self._token and not self._closing:
            self._failure = error

    def _retire_task(self, task):
        # wait(0) only polls; it never blocks GUI while native TLS cleanup ends.
        if not task.wait(0):
            QTimer.singleShot(10, lambda: self._retire_task(task))
            return
        current = task.token == self._token
        self._task = None
        task.deleteLater()
        if self._closing:
            super().reject()
            return
        if self._pending:
            kind, options = self._pending
            self._pending = None
            self._start_task(kind, **options)
            return
        self.progress.setRange(0, 1000)
        if current and self._failure:
            self.status.setText(self._failure)
            QMessageBox.warning(self, '依赖文件操作失败', self._failure)
        elif current and self._result:
            if task.kind == 'scan':
                if not self._received_batches:
                    self.model.append_batch(self._result['files'])
                self.model.finish_scan()
                self.status.setText(f'扫描完成 · {sum(item["present"] for item in self.model.rows):,} 个文件')
                if self.record_path and self.record is None:
                    self._start_task('import', path=Path(self.record_path), restore_selection=False)
                    return
            else:
                self.record = self._result['record']
                self.record_path = self._result['path']
                files = self.record['files']
                if task.kind == 'save' or task.options.get('restore_selection', True):
                    self.model.set_selection([item['path'] for item in files], files)
                self.record_input.setText(self.record_path)
                self.package_input.setText(str((self.record.get('package') or {}).get('path', '')))
                if task.kind == 'save':
                    self.model.mark_hashed(files)
                    self.status.setText('已导出记录与依赖包' if self._result.get('portable') else '已保存选择记录')
                else:
                    self.status.setText('已导入 · 待校验')
            self.progress.setValue(1000)
        else:
            self.status.setText('任务已取消')
        self.update_selection()
        self.update_controls()

    def update_filters(self, *_):
        minimum, maximum = self.size_filter.currentData()
        self.proxy.set_filters(self.search.text(), self.dll_filter.isChecked(),
            self.selected_filter.isChecked(), minimum, maximum)

    def update_selection(self):
        self.selection_label.setText(f'已选 {len(self.model.selected_paths())} 个 DLL')
        self.update_controls()

    def update_controls(self):
        scanning = bool(self._task and self._task.kind == 'scan')
        editing = not self.busy or scanning
        self.table.setEnabled(editing)
        for control in (self.select_rows_button, self.clear_rows_button):
            control.setEnabled(editing)
        self.select_filtered_button.setEnabled(not self.busy and self.model.scan_complete)
        self.save_button.setEnabled(not self.busy and self.model.scan_complete and not self.model.missing_paths())
        self.import_button.setEnabled(not self.busy)
        self.root_button.setEnabled(not self.busy or scanning)
        self.apply_button.setEnabled(not self.busy)
        self.cancel_button.setVisible(self.busy)

    def check_highlighted(self, checked):
        paths = [self.model.rows[self.proxy.mapToSource(index).row()]['path']
                 for index in self.table.selectionModel().selectedRows(0)]
        self.model.set_checked(paths, checked)

    def check_filtered(self):
        if not self.busy and self.model.scan_complete:
            self.model.set_checked(self.proxy.visible_paths(), True)

    def choose_root(self):
        root = QFileDialog.getExistingDirectory(self, '选择发布目录', str(self.root))
        if root:
            self.set_root(Path(root))

    def set_root(self, root):
        selected = self.model.selected_paths()
        self.root = Path(root).resolve()
        self.root_input.setText(str(self.root))
        previous_model = self.model
        self.model = DependencyFileModel(selected, self)
        self.model.selectionChanged.connect(self.update_selection)
        self.proxy.setSourceModel(self.model)
        previous_model.deleteLater()
        self._start_task('scan')
        self.update_selection()

    def choose_record(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入依赖记录', str(self.root), '依赖记录 (*.json)')
        if path:
            self.import_record_path(Path(path))

    def import_record_path(self, path):
        self._start_task('import', path=Path(path))

    def choose_save(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存依赖记录', f'{self.role}-依赖记录.json', '依赖记录 (*.json)')
        if path:
            self.save_record_path(Path(path))

    def save_record_path(self, path):
        if self.busy or not self.model.scan_complete:
            return
        path = Path(path).resolve()
        companions = path.with_suffix('.files').resolve()
        if any(target.is_relative_to(self.root) for target in (path, companions)):
            QMessageBox.warning(self, '请选择源目录外的位置', '记录文件及伴随依赖包目录必须保存在发布源目录之外。')
            return
        if self.model.missing_paths():
            QMessageBox.warning(self, '缺少依赖文件', '\n'.join(self.model.missing_paths()))
            return
        self._start_task('save', path=Path(path), paths=self.model.selected_paths(),
                         workers=self.workers, record=copy.deepcopy(self.record))

    def cancel_task(self):
        if self._task:
            self._task.cancel.set()
            self.status.setText('正在取消…')

    def apply_selection(self):
        if not self.busy:
            self.selectionApplied.emit(self.role, str(self.root), self.model.selected_paths(), self.record_path)
            super().accept()

    def reject(self):
        if self.busy:
            self._closing = True
            self._pending = None
            self._token += 1
            self.cancel_task()
            self.close_button.setEnabled(False)
            self.apply_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            event.accept()
