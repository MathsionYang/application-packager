"""Prototype colors, native controls, and self-contained SVG icons."""
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QLabel, QPushButton, QHBoxLayout,
                               QVBoxLayout, QWidget, QSizePolicy, QCheckBox,
                               QStyleOptionButton, QStyle)

BLUE = '#3965ee'
ICONS = {
    'box': '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="M3 8v9l9 5 9-5V8M12 13v9M7.5 5.5l9 5"/>',
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'settings': '<path d="m9 3-.6 3-2.7 1L3 6l-1 3 2.2 2v2L2 15l1 3 2.7-1 2.7 1 .6 3h6l.6-3 2.7-1 2.7 1 1-3-2.2-2v-2L22 9l-1-3-2.7 1-2.7-1L15 3H9Z"/><circle cx="12" cy="12" r="3"/>',
    'folder': '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M3 9h18"/>',
    'key': '<circle cx="8" cy="8" r="5"/><path d="m11.5 11.5 9 9M16 16l3-3M18.5 18.5l3-3"/>',
    'cpu': '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
    'app': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M8 6h.01M12 6h.01m-4 7h8m-8 4h5"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'x': '<path d="m6 6 12 12M6 18 18 6"/>',
    'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    'down': '<path d="m6 9 6 6 6-6"/>',
    'up': '<path d="m6 15 6-6 6 6"/>',
    'file': '<path d="M14 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V10l-7-7Z"/><path d="M14 3v7h7M7 14h10m-10 3h6"/>',
    'eye': '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    'upload': '<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
    'terminal': '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 8 4 4-4 4m7 0h5"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
    'layers': '<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    'save': '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
    'shield': '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
}


def icon(name, color='#8290a8', size=20):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, ICONS["file"])}</svg>'
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def label(text='', style='muted', wrap=False):
    result = QLabel(text)
    result.setProperty('kind', style)
    result.setWordWrap(wrap)
    result.setTextFormat(Qt.TextFormat.PlainText)
    if style in ('tag', 'tag-gray', 'step'):
        result.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return result


class WorkshopCheckBox(QCheckBox):
    """Draw the white check explicitly over the prototype's blue indicator."""
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('white'), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(rect.left() + 4, rect.top() + 8, rect.left() + 7, rect.top() + 11)
            painter.drawLine(rect.left() + 7, rect.top() + 11, rect.left() + 12, rect.top() + 5)
            painter.end()


def button(text, glyph=None, kind='white', callback=None):
    result = QPushButton(text)
    result.setProperty('kind', kind)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        result.setIcon(icon(glyph, '#ffffff' if kind == 'primary' else BLUE if kind == 'link' else '#8290a8'))
        result.setIconSize(QSize(16, 16))
    if callback:
        result.clicked.connect(callback)
    return result


def icon_label(glyph, color='#8290a8', background=None, size=36):
    result = QLabel()
    result.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result.setFixedSize(size, size)
    result.setPixmap(icon(glyph, color, 21).pixmap(QSize(21, 21)))
    if background:
        result.setStyleSheet(f'background:{background};border-radius:9px;')
    return result


def box(vertical=True, margins=(0, 0, 0, 0), spacing=0):
    widget = QWidget()
    layout = QVBoxLayout(widget) if vertical else QHBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return widget, layout


def card(margins=(22, 20, 22, 20), spacing=12):
    widget = QFrame()
    widget.setProperty('kind', 'card')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return widget, layout


def line():
    result = QFrame()
    result.setFixedHeight(1)
    result.setStyleSheet('background:#edf0f6;border:0;')
    return result


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


STYLESHEET = '''
QWidget { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 12px; color: #202b42; }
QMainWindow, QWidget#content, QScrollArea, QWidget#page { background: #f5f7fb; }
QWidget#sidebar, QWidget#topbar { background: white; }
QWidget#sidebar { border-right: 1px solid #e8ecf2; }
QWidget#topbar { border-bottom: 1px solid #e8ecf2; }
QFrame[kind="card"] { background: white; border: 1px solid #e8ecf2; border-radius: 12px; }
QFrame[kind="path"] { background: #fbfcfe; border: 1px solid #e5eaf2; border-radius: 7px; }
QLabel { background: transparent; border: 0; }
QLabel[kind="muted"] { color: #8d99ad; font-size: 11px; }
QLabel[kind="tiny"] { color: #a0aabc; font-size: 10px; }
QLabel[kind="heading"] { font-size: 25px; font-weight: 650; color: #202b42; }
QLabel[kind="eyebrow"] { color: #8d9ab0; font-size: 9px; font-weight: 600; }
QLabel[kind="section"] { font-size: 12px; font-weight: 650; color: #202b42; }
QLabel[kind="field"] { font-size: 11px; font-weight: 600; color: #4a5870; }
QLabel[kind="blue"] { color: #3965ee; }
QLabel[kind="error"] { color: #d95362; font-size: 11px; }
QLabel[kind="step"] { background: #f2f5fa; color: #9eacc6; border-radius: 4px; padding: 3px 5px; font-size: 10px; font-weight:600; }
QLabel[kind="tag"] { color:#6483df; background:#edf2ff; border-radius:4px; padding:3px 5px; font-size:9px; }
QLabel[kind="tag-gray"] { color:#9aa4b4; background:#f3f5f8; border-radius:4px; padding:3px 5px; font-size:9px; }
QPushButton { border: 1px solid #e1e6ef; border-radius: 7px; background:white; color:#5a6881; padding:10px 14px; font-size:11px; font-weight:500; }
QPushButton:hover { background:#f9faff; border-color:#bccaf1; }
QPushButton:pressed { background:#edf2ff; }
QPushButton:disabled { color:#b5bfce; background:#f4f6fa; border-color:#edf0f6; }
QPushButton[kind="primary"] { background:#3965ee; border-color:#3965ee; color:white; }
QPushButton[kind="primary"]:hover { background:#2a53d4; }
QPushButton[kind="primary"]:disabled { background:#a3b5ef; border-color:#a3b5ef; color:#f4f7ff; }
QPushButton[kind="text"], QPushButton[kind="link"] { background:transparent; border-color:transparent; }
QPushButton[kind="link"] { color:#3965ee; padding:4px 0; }
QPushButton[kind="text"]:hover, QPushButton[kind="link"]:hover { background:#eaf0fa; color:#3965ee; }
QPushButton[kind="small"] { padding:5px 9px; font-size:10px; }
QPushButton[kind="icon"] { padding:5px; background:transparent; border:0; border-radius:5px; }
QPushButton[kind="icon"]:hover { background:#edf2fa; }
QPushButton[kind="browse"] { padding:8px 11px; border:0; border-left:1px solid #e5eaf2; border-radius:0; font-size:10px; }
QPushButton[kind="nav"] { text-align:left; border:0; background:transparent; color:#788398; padding:13px 14px; border-radius:8px; font-size:12px; }
QPushButton[kind="nav"]:checked { color:#3965ee; background:#edf2ff; font-weight:600; }
QPushButton[kind="nav"]:hover { background:#f5f7fc; }
QLineEdit, QComboBox { background:white; color:#4f5e77; border:1px solid #e5eaf2; border-radius:7px; padding:9px 11px; font-size:11px; selection-background-color:#dce6ff; }
QLineEdit:focus, QComboBox:focus { border-color:#8aa4f5; }
QLineEdit[kind="path"] { background:transparent; border:0; padding:9px 11px; }
QCheckBox { spacing:8px; font-size:12px; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #cdd6e7; border-radius:4px; background:white; }
QCheckBox::indicator:checked { background:#3965ee; border-color:#3965ee; image:none; }
QCheckBox::indicator:disabled:checked { background:#8ba5ef; border-color:#8ba5ef; }
QProgressBar { border:0; background:#edf2fc; border-radius:3px; text-align:center; }
QProgressBar::chunk { background:#3965ee; border-radius:3px; }
QPlainTextEdit { border:0; border-top:1px solid #eef2f7; background:#f9fbfe; padding:10px; color:#8090ab; font-family:Consolas,"Microsoft YaHei UI"; font-size:10px; }
QScrollArea { border:0; }
QScrollBar:vertical { background:transparent; width:6px; margin:0; }
QScrollBar::handle:vertical { background:#d3d9e4; min-height:30px; border-radius:3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QDialog { background:white; }
QToolTip { background:#263852; color:white; border:0; padding:6px; }
QMenu { background:white; border:1px solid #e5eaf2; padding:5px; }
QMenu::item { padding:8px 20px; }
QMenu::item:selected { background:#edf2ff; color:#3965ee; }
'''
