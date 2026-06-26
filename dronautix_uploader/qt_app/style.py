"""QSS theme for the Qt app.

Dark Dronautix theme (wie die Legacy-CustomTkinter-App: appearance_mode "dark",
Akzent-Blau, Consolas-Logs), kombiniert mit dem Marken-Navy aus icon.ico.

Palette:
- Surface (Hauptscreen):  #1b2c46  (Marken-Navy)
- Sidebar:                #16243b -> #101d33 (Verlauf, etwas tiefer)
- Card / Panel / Input:   #233650
- Card hover:             #2b4063
- Rahmen:                 #35527d / weich #2c4163
- Text:                   #e2e8f0   dim #94a3b8   schwach #64748b
- Akzent:                 #3b82f6   hover #2563eb   pressed #1d4ed8
- Erfolg:                 #2ecc71   Gefahr #e74c3c
"""

APP_STYLE = """
QWidget {
    color: #e2e8f0;
}

QWidget#AppRoot {
    background: #1b2c46;
    color: #e2e8f0;
}

QWidget#Page {
    background: #1b2c46;
}

/* ---------- Sidebar ---------- */
QFrame#Sidebar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #16243b, stop:1 #101d33);
    border: none;
}

QLabel#SidebarTitle {
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
}

QLabel#SidebarSubtitle,
QLabel#PreviewBadge {
    color: #8595ad;
    font-size: 12px;
}

QPushButton#SidebarButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #c6d2e4;
    min-height: 38px;
    padding: 0 14px;
    text-align: left;
}

QPushButton#SidebarButton:hover {
    background: #243b59;
    color: #ffffff;
}

QPushButton#SidebarButton:pressed {
    background: #101d33;
    color: #ffffff;
    padding-left: 16px;
    padding-top: 1px;
}

QPushButton#SidebarButton:checked {
    background: #3b82f6;
    color: #ffffff;
    font-weight: 600;
}

/* ---------- Headings / text ---------- */
QLabel#PageTitle {
    color: #f1f5fb;
    font-size: 24px;
    font-weight: 700;
}

QLabel#PanelTitle {
    color: #f1f5fb;
    font-size: 16px;
    font-weight: 700;
}

QLabel#SectionTitle {
    color: #aebdd4;
    font-size: 12px;
    font-weight: 700;
}

QLabel#MutedText {
    color: #94a3b8;
}

QLabel#ErrorText {
    color: #f6a5a0;
    font-weight: 600;
}

QLabel#ActivityStatValue {
    color: #f1f5fb;
    font-size: 22px;
    font-weight: 700;
}

/* ---------- Badges ---------- */
QLabel#PreviewBadgeLight {
    background: #1e3a5f;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    color: #bcd6ff;
    font-weight: 600;
    min-height: 28px;
    padding: 0 12px;
}

QLabel#PreviewBadgeDanger {
    background: #3a1d1d;
    border: 1px solid #e74c3c;
    border-radius: 6px;
    color: #f6a5a0;
    font-weight: 600;
    min-height: 28px;
    padding: 0 12px;
}

/* ---------- Inputs ---------- */
QLineEdit,
QPlainTextEdit,
QTextEdit,
QComboBox {
    background: #233650;
    border: 1px solid #35527d;
    border-radius: 6px;
    color: #e2e8f0;
    min-height: 30px;
    padding: 0 10px;
    selection-background-color: #3b82f6;
    selection-color: #ffffff;
}

QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus,
QComboBox:focus {
    border: 1px solid #3b82f6;
}

QLineEdit:disabled,
QComboBox:disabled {
    background: #1c2c44;
    color: #6b7c98;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox QAbstractItemView {
    background: #233650;
    border: 1px solid #35527d;
    color: #e2e8f0;
    selection-background-color: #3b82f6;
    selection-color: #ffffff;
    outline: 0;
}

QLineEdit#SearchField,
QComboBox#StatusFilter {
    min-height: 36px;
}

QCheckBox {
    color: #e2e8f0;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #35527d;
    border-radius: 4px;
    background: #233650;
}

QCheckBox::indicator:checked {
    background: #3b82f6;
    border: 1px solid #3b82f6;
}

/* ---------- Tables ---------- */
QTableView#ProjectsTable,
QTableView#ActivityTable {
    background: #1f3150;
    alternate-background-color: #233650;
    border: 1px solid #35527d;
    border-radius: 6px;
    color: #e2e8f0;
    gridline-color: #2c4163;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
    outline: 0;
}

QTableView#ProjectsTable::item,
QTableView#ActivityTable::item {
    color: #e2e8f0;
    min-height: 32px;
    padding: 6px 8px;
}

QTableView#ProjectsTable::item:selected,
QTableView#ActivityTable::item:selected {
    background: #2563eb;
    color: #ffffff;
}

QHeaderView::section {
    background: #16243b;
    border: none;
    border-bottom: 1px solid #35527d;
    color: #aebdd4;
    font-weight: 600;
    min-height: 34px;
    padding: 0 8px;
}

QTableCornerButton::section {
    background: #16243b;
    border: none;
}

/* ---------- Panels / cards ---------- */
QFrame#DetailPanel,
QFrame#ActivityStatCard,
QFrame#WizardStepPanel,
QFrame#LogPanel {
    background: #233650;
    border: 1px solid #35527d;
    border-radius: 6px;
}

QFrame#SettingsStatusRow {
    background: #1f3150;
    border: 1px solid #2c4163;
    border-radius: 6px;
}

QPushButton#DashboardCardButton {
    background: #233650;
    border: 1px solid #35527d;
    border-radius: 6px;
    min-height: 106px;
    padding: 0;
    text-align: left;
}

QPushButton#DashboardCardButton:hover {
    background: #2b4063;
    border: 1px solid #3b82f6;
}

/* ---------- Wizard steps (local conversion) ---------- */
QFrame#WizardStep_done {
    background: #14352a;
    border: 1px solid #2ecc71;
    border-radius: 6px;
}

QFrame#WizardStep_current {
    background: #16314f;
    border: 1px solid #3b82f6;
    border-radius: 6px;
}

QFrame#WizardStep_upcoming {
    background: #1f3150;
    border: 1px solid #2c4163;
    border-radius: 6px;
}

QLabel#WizardStepMarker {
    background: #3b82f6;
    border-radius: 14px;
    color: #ffffff;
    font-weight: 700;
}

/* ---------- Upload / lists ---------- */
QFrame#UploadDropZone {
    background: #1f3150;
    border: 2px dashed #4b6a99;
    border-radius: 8px;
}

QListWidget#PointcloudList,
QListWidget#UploadSourceList {
    background: #1f3150;
    border: 1px solid #2c4163;
    border-radius: 6px;
    color: #e2e8f0;
    padding: 6px;
    outline: 0;
}

QListWidget#PointcloudList::item {
    min-height: 28px;
    padding: 4px 6px;
}

QListWidget#UploadSourceList::item {
    min-height: 34px;
    padding: 5px 6px;
}

QListWidget::item:selected {
    background: #2563eb;
    color: #ffffff;
    border-radius: 4px;
}

QLabel#LogLine {
    background: #16243b;
    border: 1px solid #2c4163;
    border-radius: 6px;
    color: #c6d2e4;
    padding: 7px 9px;
}

QPlainTextEdit#UploadLogView {
    background: #0f1c33;
    border: 1px solid #2c4163;
    border-radius: 6px;
    color: #c6d2e4;
    font-family: "Consolas", "Courier New", monospace;
    padding: 8px;
}

/* ---------- Buttons ---------- */
QPushButton#ActionButton,
QToolButton#ActionButton {
    background: #28406a;
    border: 1px solid #3a567f;
    border-radius: 6px;
    color: #e6edf7;
    min-height: 34px;
    padding: 0 12px;
}

QPushButton#ActionButton:hover,
QToolButton#ActionButton:hover {
    background: #324f80;
    border: 1px solid #3b82f6;
    color: #ffffff;
}

QPushButton#ActionButton:pressed {
    background: #1d4ed8;
    border: 1px solid #1d4ed8;
    color: #ffffff;
    padding-left: 13px;
    padding-top: 1px;
}

QPushButton#ActionButton:checked {
    background: #3b82f6;
    border: 1px solid #3b82f6;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#ActionButton:disabled,
QToolButton#ActionButton:disabled {
    background: #1f3150;
    border: 1px solid #2c4163;
    color: #5f6f8b;
}

QToolButton#ActionButton::menu-indicator {
    image: none;
    width: 0;
}

QToolButton#AdvancedToggle {
    background: transparent;
    border: none;
    color: #aebdd4;
    font-weight: 600;
    padding: 4px 0;
}

QToolButton#AdvancedToggle:hover {
    color: #3b82f6;
}

QPushButton#PrimaryButton {
    background: #3b82f6;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    color: #ffffff;
    font-weight: 600;
    min-height: 34px;
    padding: 0 18px;
}

QPushButton#PrimaryButton:hover {
    background: #2563eb;
    border: 1px solid #2563eb;
}

QPushButton#PrimaryButton:pressed {
    background: #1d4ed8;
    border: 1px solid #1d4ed8;
    padding-top: 1px;
}

QPushButton#PrimaryButton:disabled {
    background: #2a4068;
    border: 1px solid #2a4068;
    color: #7b8db0;
}

/* ---------- Progress ---------- */
QProgressBar#UploadProgress {
    background: #1f3150;
    border: 1px solid #35527d;
    border-radius: 6px;
    color: #e2e8f0;
    min-height: 30px;
    text-align: center;
}

QProgressBar#UploadProgress::chunk {
    background: #3b82f6;
    border-radius: 5px;
}

QLabel#UploadStatusLine {
    color: #bcd6ff;
    font-weight: 600;
    padding: 2px 0;
}

/* ---------- Generic buttons / dialogs / menus ---------- */
QPushButton {
    background: #28406a;
    border: 1px solid #3a567f;
    border-radius: 6px;
    color: #e6edf7;
    min-height: 30px;
    padding: 0 12px;
}

QPushButton:hover {
    background: #324f80;
    border: 1px solid #3b82f6;
}

QPushButton:pressed {
    background: #1d4ed8;
    border: 1px solid #1d4ed8;
    padding-top: 1px;
}

QPushButton:disabled {
    background: #1f3150;
    border: 1px solid #2c4163;
    color: #5f6f8b;
}

QDialog,
QMessageBox {
    background: #1b2c46;
    color: #e2e8f0;
}

QMenu {
    background: #233650;
    border: 1px solid #35527d;
    color: #e2e8f0;
    padding: 4px;
}

QMenu::item {
    padding: 6px 18px;
    border-radius: 4px;
}

QMenu::item:selected {
    background: #3b82f6;
    color: #ffffff;
}

QMenu::item:disabled {
    color: #5f6f8b;
}

QMenu::separator {
    background: #35527d;
    height: 1px;
    margin: 4px 8px;
}

QToolTip {
    background: #16243b;
    border: 1px solid #35527d;
    color: #e2e8f0;
    padding: 4px 6px;
}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #34507a;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #3b82f6;
}

QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: #34507a;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: #3b82f6;
}

QScrollBar::add-line,
QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QScrollBar::add-page,
QScrollBar::sub-page {
    background: transparent;
}

QStatusBar {
    background: #16243b;
    color: #94a3b8;
}

QStatusBar::item {
    border: none;
}
"""
