"""
EhViewer漫画管理工具 - PyQt5 图形界面
"""

import os
import sys
import traceback
from typing import Optional, List

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QObject, QSize
from PyQt5.QtGui import QFont, QTextCursor, QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QTabWidget,
    QTextEdit,
    QFileDialog,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QSplitter,
    QFrame,
    QMessageBox,
    QSizePolicy,
)

from .config import DEFAULT_THRESHOLD
from .manager import MangaManager

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _set_bg(widget, r: int, g: int, b: int):
    """通过 palette 设置控件背景色，不触发 QSS 子控件样式重算。"""
    widget.setAutoFillBackground(True)
    p = widget.palette()
    p.setColor(QPalette.Window, QColor(r, g, b))
    widget.setPalette(p)


# ---------------------------------------------------------------------------
# 帮助提示小标签
# ---------------------------------------------------------------------------


def _tip(tooltip: str) -> QLabel:
    lbl = QLabel("?")
    lbl.setToolTip(tooltip)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setCursor(Qt.WhatsThisCursor)
    lbl.setFixedSize(17, 17)
    lbl.setStyleSheet(
        "QLabel { color:#7ab3e0; font-weight:bold; font-size:10px;"
        " border:1px solid #5090c0; border-radius:8px; background:#243040; }"
        "QLabel:hover { background:#3a5070; }"
    )
    return lbl


# ---------------------------------------------------------------------------
# 工作线程
# ---------------------------------------------------------------------------


class WorkerSignals(QObject):
    log = pyqtSignal(str, str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)


class BaseWorker(QThread):
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self._manager: Optional[MangaManager] = None

    def _log(self, msg: str, level: str = "info"):
        self.signals.log.emit(msg, level)

    def _init_manager(self) -> bool:
        self._manager = MangaManager()
        import builtins

        orig = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            lv = "info"
            if any(k in msg for k in ["失败", "错误", "Error", "error"]):
                lv = "error"
            elif any(k in msg for k in ["警告", "Warning", "warn"]):
                lv = "warn"
            elif any(k in msg for k in ["✓", "完成", "成功", "已拉取", "已连接"]):
                lv = "ok"
            self._log(msg, lv)
            orig(*args, **kwargs)

        builtins.print = gui_print
        try:
            ok = self._manager.initialize()
        finally:
            builtins.print = orig
        return ok

    def _cleanup(self):
        if self._manager:
            self._manager.cleanup()


class ConnectWorker(BaseWorker):
    def run(self):
        try:
            if self._init_manager():
                self.signals.finished.emit(self._manager)
            else:
                self.signals.error.emit("初始化失败，请检查 adb 连接")
                self._cleanup()
        except Exception as e:
            self.signals.error.emit(f"连接出错: {e}\n{traceback.format_exc()}")
            self._cleanup()


class AnalyzeWorker(BaseWorker):
    def __init__(self, manager: MangaManager, threshold: float):
        super().__init__()
        self._manager = manager
        self.threshold = threshold

    def _init_manager(self) -> bool:
        return True

    def run(self):
        import builtins

        orig = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            lv = "ok" if "[达标]" in msg else ("error" if "[失败]" in msg else "info")
            self._log(msg, lv)
            orig(*args, **kwargs)

        builtins.print = gui_print
        try:
            self.signals.finished.emit(
                self._manager.analyze_reading_progress(self.threshold)
            )
        except Exception as e:
            self.signals.error.emit(f"分析出错: {e}")
        finally:
            builtins.print = orig


class MoveWorker(BaseWorker):
    def __init__(
        self,
        manager: MangaManager,
        results: list,
        dest_dir: str,
        remove: bool,
        sync_db: bool,
    ):
        super().__init__()
        self._manager = manager
        self.results = results
        self.dest_dir = dest_dir
        self.remove = remove
        self.sync_db = sync_db

    def _init_manager(self) -> bool:
        return True

    def run(self):
        import builtins

        orig = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            lv = "info"
            if any(k in msg for k in ["已拉取", "成功", "已删除"]):
                lv = "ok"
            elif any(k in msg for k in ["失败", "错误"]):
                lv = "error"
            elif "警告" in msg:
                lv = "warn"
            self._log(msg, lv)
            orig(*args, **kwargs)

        builtins.print = gui_print
        try:
            os.makedirs(self.dest_dir, exist_ok=True)
            moved, failed = [], []
            for manga in self.results:
                ok = self._manager.move_manga_to_pc(
                    manga,
                    self.dest_dir,
                    remove_from_phone=self.remove,
                    dry_run=False,
                )
                (moved if ok else failed).append(manga["gid"] if ok else manga["title"])

            if self.sync_db and moved:
                self._log(f"\n正在清理 {len(moved)} 条数据库记录...", "info")
                deleted = self._manager.clean_database_records(moved)
                self._log(f"已清理 {deleted}/{len(moved)} 条记录", "ok")
                self._manager.create_backup_and_push()

            self.signals.finished.emit({"moved": moved, "failed": failed})
        except Exception as e:
            self.signals.error.emit(f"移动出错: {e}\n{traceback.format_exc()}")
        finally:
            builtins.print = orig


# ---------------------------------------------------------------------------
# 日志组件
# ---------------------------------------------------------------------------


class LogWidget(QTextEdit):
    COLORS = {"info": "#d8d8d8", "ok": "#6fcf6f", "warn": "#f0c040", "error": "#f07070"}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 11))
        self.setStyleSheet(
            "background:#1a1a1a; color:#d8d8d8;"
            " border:1px solid #3a3a3a; border-radius:4px;"
        )

    @pyqtSlot(str, str)
    def append_log(self, msg: str, level: str = "info"):
        color = self.COLORS.get(level, self.COLORS["info"])
        esc = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.append(f'<span style="color:{color};">{esc}</span>')
        self.moveCursor(QTextCursor.End)

    def clear_log(self):
        self.clear()


# ---------------------------------------------------------------------------
# 选项卡: 移动漫画
# ---------------------------------------------------------------------------


class MoveTab(QWidget):
    request_analyze = pyqtSignal(float)
    request_move = pyqtSignal(list, str, bool, bool)

    def __init__(self):
        super().__init__()
        self._results: List[dict] = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        # ── 目标目录 ──────────────────────────────────────
        dest_box = QGroupBox("目标目录")
        dest_row = QHBoxLayout(dest_box)
        dest_row.setContentsMargins(10, 8, 10, 8)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("选择或输入本地存储路径…")
        self.dest_edit.setToolTip("漫画将拉取到此目录，每本保存为独立子文件夹")
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(72)
        browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.dest_edit)
        dest_row.addWidget(browse_btn)
        outer.addWidget(dest_box)

        # ── 选项 (2行) ────────────────────────────────────
        opt_box = QGroupBox("选项")
        opt_vlay = QVBoxLayout(opt_box)
        opt_vlay.setContentsMargins(10, 10, 10, 10)
        opt_vlay.setSpacing(10)

        # 第1行: 数值控件
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        thr_lbl = QLabel("阅读进度阈值:")
        thr_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.1, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(DEFAULT_THRESHOLD)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setFixedWidth(96)
        _thr_tip = _tip(
            "只有阅读完成度 ≥ 此比例的漫画才会进入列表\n" "例如 0.90 = 已读超过 90%"
        )
        self.threshold_spin.setToolTip(_thr_tip.toolTip())

        batch_lbl = QLabel("批次大小:")
        batch_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(0, 9999)
        self.batch_spin.setValue(0)
        self.batch_spin.setSpecialValueText("全部")
        self.batch_spin.setFixedWidth(96)
        _bat_tip = _tip(
            "单次执行移动的最多漫画数量\n"
            "0（全部）= 一次性处理所有达标漫画\n"
            "可设较小值分批操作"
        )
        self.batch_spin.setToolTip(_bat_tip.toolTip())

        row1.addWidget(thr_lbl)
        row1.addWidget(_thr_tip)
        row1.addWidget(self.threshold_spin)
        row1.addSpacing(24)
        row1.addWidget(batch_lbl)
        row1.addWidget(_bat_tip)
        row1.addWidget(self.batch_spin)
        row1.addStretch()
        opt_vlay.addLayout(row1)

        # 第2行: 复选框
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.remove_chk = QCheckBox("移动后删除手机原文件")
        _rm_tip = _tip("漫画拉取成功后自动删除手机上\n" "对应的文件夹，释放手机存储")
        self.remove_chk.setToolTip(_rm_tip.toolTip())

        self.sync_chk = QCheckBox("同步更新 EhViewer 数据库")
        _sync_tip = _tip(
            "从数据库删除已移走的条目并\n" "推送回手机，App 将不再显示\n" "已移走的漫画"
        )
        self.sync_chk.setToolTip(_sync_tip.toolTip())

        row2.addWidget(self.remove_chk)
        row2.addWidget(_rm_tip)
        row2.addSpacing(24)
        row2.addWidget(self.sync_chk)
        row2.addWidget(_sync_tip)
        row2.addStretch()
        opt_vlay.addLayout(row2)

        outer.addWidget(opt_box)

        # ── 状态摘要 ──────────────────────────────────────
        self.summary_lbl = QLabel("尚未分析 — 请先点击「① 分析」")
        self.summary_lbl.setStyleSheet(
            "color:#999; padding:8px 12px; font-size:11pt;"
            " background:#252525; border-radius:4px;"
        )
        outer.addWidget(self.summary_lbl)

        # ── 操作按钮 ──────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.analyze_btn = QPushButton("① 分析进度")
        self.analyze_btn.setFixedHeight(42)
        self.analyze_btn.setMinimumWidth(110)
        self.analyze_btn.setToolTip(
            "扫描手机上所有漫画的 spiderInfo 文件\n"
            "计算阅读完成度，列出超过阈值的条目"
        )
        self.analyze_btn.clicked.connect(self._on_analyze)

        self.dryrun_btn = QPushButton("② 预演")
        self.dryrun_btn.setFixedHeight(42)
        self.dryrun_btn.setMinimumWidth(110)
        self.dryrun_btn.setEnabled(False)
        self.dryrun_btn.setToolTip(
            "预览将被移动的漫画列表（Dry Run）\n" "不执行任何实际文件操作"
        )
        self.dryrun_btn.clicked.connect(self._on_dryrun)

        self.move_btn = QPushButton("③ 执行移动")
        self.move_btn.setFixedHeight(42)
        self.move_btn.setMinimumWidth(110)
        self.move_btn.setEnabled(False)
        self.move_btn.setToolTip(
            "通过 ADB 将达标漫画拉取到本地目录\n"
            "（根据上方选项决定是否删除原文件和同步数据库）"
        )
        self.move_btn.setStyleSheet(
            "QPushButton{background:#2e5a2e;color:#fff;border-radius:4px;}"
            "QPushButton:hover{background:#3d7a3d;}"
            "QPushButton:disabled{background:#3a3a3a;color:#666;}"
        )
        self.move_btn.clicked.connect(self._on_move)

        btn_row.addWidget(self.analyze_btn)
        btn_row.addWidget(self.dryrun_btn)
        btn_row.addWidget(self.move_btn)
        outer.addLayout(btn_row)

        outer.addStretch()

    # ── 事件 ──────────────────────────────────────────────

    def _browse_dest(self):
        d = QFileDialog.getExistingDirectory(self, "选择目标目录")
        if d:
            self.dest_edit.setText(d)

    def _on_analyze(self):
        self.request_analyze.emit(self.threshold_spin.value())

    def _on_dryrun(self):
        if not self._results:
            return
        dest = self.dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "提示", "请先选择目标目录")
            return
        batch = self.batch_spin.value()
        items = self._results[:batch] if batch > 0 else self._results
        lines = [f"[DRY-RUN] 将移动 {len(items)} 个漫画到: {dest}", ""]
        for m in items:
            lines.append(f"  {m['title'][:70]}  ({m['progress']*100:.0f}%)")
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTextEdit as TE

        dlg = QDialog(self)
        dlg.setWindowTitle("Dry Run 预览")
        dlg.resize(720, 440)
        vl = QVBoxLayout(dlg)
        te = TE()
        te.setReadOnly(True)
        te.setFont(QFont("Consolas", 10))
        te.setPlainText("\n".join(lines))
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        vl.addWidget(te)
        vl.addWidget(bb)
        dlg.exec_()

    def _on_move(self):
        dest = self.dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "提示", "请先选择目标目录")
            return
        if not self._results:
            QMessageBox.warning(self, "提示", "请先执行分析")
            return
        batch = self.batch_spin.value()
        items = self._results[:batch] if batch > 0 else self._results
        if (
            QMessageBox.question(
                self,
                "确认移动",
                f"即将移动 {len(items)} 个漫画到:\n{dest}\n\n"
                f"删除手机原文件: {'是' if self.remove_chk.isChecked() else '否'}\n"
                f"同步数据库:    {'是' if self.sync_chk.isChecked() else '否'}\n\n继续?",
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self.request_move.emit(
            items, dest, self.remove_chk.isChecked(), self.sync_chk.isChecked()
        )

    def set_results(self, results: List[dict]):
        self._results = results
        n = len(results)
        if n:
            self.summary_lbl.setText(f"找到 {n} 个达标漫画，可设「批次大小」分批处理")
            self.summary_lbl.setStyleSheet(
                "color:#6fcf6f; padding:6px 8px;"
                " background:#1c2c1c; border-radius:4px;"
            )
        else:
            self.summary_lbl.setText("未找到达到阈值的漫画")
            self.summary_lbl.setStyleSheet(
                "color:#f0c040; padding:6px 8px;"
                " background:#2c2800; border-radius:4px;"
            )
        self.dryrun_btn.setEnabled(n > 0)
        self.move_btn.setEnabled(n > 0)

    def set_busy(self, busy: bool):
        self.analyze_btn.setEnabled(not busy)
        self.dryrun_btn.setEnabled(not busy and bool(self._results))
        self.move_btn.setEnabled(not busy and bool(self._results))


# ---------------------------------------------------------------------------
# 选项卡: 分析结果
# ---------------------------------------------------------------------------


class AnalyzeTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        self.info_lbl = QLabel("执行分析后结果显示于此")
        self.info_lbl.setStyleSheet("color:#888;")
        lay.addWidget(self.info_lbl)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["GID", "标题", "进度", "页数", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeToContents
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)

    def populate(self, results: List[dict]):
        self.table.setRowCount(0)
        n = len(results)
        self.info_lbl.setText(f"共 {n} 个达标漫画" if n else "暂无达标漫画")
        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(r["gid"])))
            self.table.setItem(row, 1, QTableWidgetItem(r["title"]))
            self.table.setItem(row, 2, QTableWidgetItem(f"{r['progress']*100:.1f}%"))
            self.table.setItem(
                row, 3, QTableWidgetItem(f"{r['current_page']+1}/{r['total_pages']}")
            )
            self.table.setItem(row, 4, QTableWidgetItem(r["state_text"]))


# ---------------------------------------------------------------------------
# 选项卡: 文件名检查
# ---------------------------------------------------------------------------


class FilenameTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        top_row = QHBoxLayout()
        self.label = QLabel("点击「检查」扫描数据库中含 Windows 非法字符的目录名")
        self.label.setStyleSheet("color:#aaa;")
        self.check_btn = QPushButton("检查文件名兼容性")
        self.check_btn.setFixedWidth(160)
        self.check_btn.setFixedHeight(36)
        self.check_btn.setToolTip(
            "扫描所有漫画目录名中含有 Windows 非法字符\n"
            '（: * ? " < > |）的条目，移动时自动净化'
        )
        top_row.addWidget(self.label)
        top_row.addStretch()
        top_row.addWidget(self.check_btn)
        lay.addLayout(top_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["GID", "原始目录名", "净化后目录名"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)

    def populate(self, issues: List[dict]):
        self.table.setRowCount(0)
        if not issues:
            self.label.setText("✓ 所有目录名均兼容 Windows，无需净化")
            self.label.setStyleSheet("color:#6fcf6f;")
            return
        self.label.setText(f"发现 {len(issues)} 个需净化的目录名（移动时自动处理）")
        self.label.setStyleSheet("color:#f0c040;")
        for item in issues:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(item["gid"])))
            self.table.setItem(row, 1, QTableWidgetItem(item["original"]))
            self.table.setItem(row, 2, QTableWidgetItem(item["sanitized"]))


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EhViewer 漫画管理工具")
        self.resize(1200, 760)
        self.setMinimumSize(900, 580)
        self._manager: Optional[MangaManager] = None
        self._worker: Optional[QThread] = None
        self._results: List[dict] = []
        self._build_ui()
        self._auto_connect()

    # ── UI 构建 ────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── 顶部状态栏 ── 用 palette 设置背景，避免 QSS 影响子控件颜色
        bar = QFrame()
        bar.setFixedHeight(50)
        bar.setObjectName("topBar")
        # 下边框用 setStyleSheet 只作用于 QFrame 本身，不影响子控件
        bar.setStyleSheet("QFrame#topBar { border-bottom: 1px solid #2e4050; }")
        _set_bg(bar, 0x1E, 0x2A, 0x35)

        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(14, 0, 14, 0)
        bar_lay.setSpacing(10)

        self.conn_lbl = QLabel("● 未连接")
        self.conn_lbl.setStyleSheet(
            "color:#f07070; font-weight:bold; font-size:11pt; background:transparent;"
        )
        # 防止设备 ID 过长时把右侧按钮挤出视野
        self.conn_lbl.setMaximumWidth(520)
        self.conn_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedSize(150, 14)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #3a5060;border-radius:7px;background:#0f1820;}"
            "QProgressBar::chunk{background:#3a7ab0;border-radius:7px;}"
        )

        self.reconnect_btn = QPushButton("重连")
        self.reconnect_btn.setFixedSize(100, 32)
        self.reconnect_btn.setToolTip("通过 ADB 重新连接手机并拉取最新数据库")
        self.reconnect_btn.clicked.connect(self._auto_connect)

        help_btn = QPushButton("帮助")
        help_btn.setFixedSize(64, 32)
        help_btn.setToolTip("查看使用说明")
        help_btn.clicked.connect(self._show_help)

        # stretch 在 label 和按钮之间 —— 按钮永远贴右侧，有充足空间
        bar_lay.addWidget(self.conn_lbl)
        bar_lay.addStretch()
        bar_lay.addWidget(self.progress_bar)
        bar_lay.addSpacing(6)
        bar_lay.addWidget(self.reconnect_btn)
        bar_lay.addWidget(help_btn)
        root_lay.addWidget(bar)

        # ── 主体: 左选项卡 + 右日志 ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle{background:#2e3e4e;}")

        # 左: 选项卡
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.move_tab = MoveTab()
        self.move_tab.request_analyze.connect(self._on_request_analyze)
        self.move_tab.request_move.connect(self._on_request_move)
        self.tabs.addTab(self.move_tab, "  移动漫画  ")

        self.analyze_tab = AnalyzeTab()
        self.tabs.addTab(self.analyze_tab, "  分析结果  ")

        self.filename_tab = FilenameTab()
        self.filename_tab.check_btn.clicked.connect(self._on_check_names)
        self.tabs.addTab(self.filename_tab, "  文件名检查  ")

        splitter.addWidget(self.tabs)

        # 右: 日志 — 用 palette 设置背景，不用 setStyleSheet 避免破坏子控件
        log_panel = QWidget()
        _set_bg(log_panel, 0x18, 0x18, 0x18)
        log_lay = QVBoxLayout(log_panel)
        log_lay.setContentsMargins(0, 0, 0, 0)
        log_lay.setSpacing(0)

        log_hdr = QFrame()
        log_hdr.setFixedHeight(42)
        log_hdr.setObjectName("logHdr")
        log_hdr.setStyleSheet("QFrame#logHdr { border-bottom: 1px solid #2e4050; }")
        _set_bg(log_hdr, 0x1E, 0x2A, 0x35)

        log_hdr_lay = QHBoxLayout(log_hdr)
        log_hdr_lay.setContentsMargins(12, 0, 8, 0)
        log_hdr_lay.setSpacing(6)
        log_title = QLabel("运行日志")
        log_title.setStyleSheet("font-weight:bold; font-size:10pt;")
        clear_btn = QPushButton("清空")
        clear_btn.setFixedSize(80, 28)
        clear_btn.clicked.connect(lambda: self.log.clear_log())
        log_hdr_lay.addWidget(log_title)
        log_hdr_lay.addStretch()
        log_hdr_lay.addWidget(clear_btn)

        self.log = LogWidget()
        log_lay.addWidget(log_hdr)
        log_lay.addWidget(self.log)

        splitter.addWidget(log_panel)

        # 3:2 比例，窗口缩放时按比例分配
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([720, 480])

        root_lay.addWidget(splitter)

    # ── 帮助 ────────────────────────────────────────────────

    def _show_help(self):
        QMessageBox.information(
            self,
            "使用说明",
            "【EhViewer 漫画管理工具 使用流程】\n\n"
            "1. 用 USB 连接手机，确保已开启 ADB 调试\n"
            "   程序启动后自动连接并拉取数据库\n\n"
            "2. 在「移动漫画」选项卡中:\n"
            "   · 选择「目标目录」（本地存储路径）\n"
            "   · 调整「阅读进度阈值」（默认 0.90 = 已读 90%）\n"
            "   · 点击「① 分析」扫描所有漫画进度\n"
            "   · 可选「② 预演」确认移动列表\n"
            "   · 点击「③ 执行移动」开始传输\n\n"
            "3. 选项说明:\n"
            "   · 批次大小: 0 = 一次处理全部；>0 = 分批\n"
            "   · 删除手机原文件: 移动成功后删除手机端\n"
            "   · 同步数据库: 清理已移走条目并推回手机\n\n"
            "4. 「文件名检查」可预览含 Windows 非法字符的\n"
            "   目录名；执行移动时会自动净化",
        )

    # ── 连接 ────────────────────────────────────────────────

    def _auto_connect(self):
        self._set_busy(True, "正在连接设备并拉取数据库…")
        self.conn_lbl.setText("● 连接中…")
        self.conn_lbl.setStyleSheet(
            "color:#f0c040; font-weight:bold; font-size:11pt; background:transparent;"
        )
        if self._manager:
            self._manager.cleanup()
            self._manager = None

        w = ConnectWorker()
        w.signals.log.connect(self.log.append_log)
        w.signals.finished.connect(self._on_connected)
        w.signals.error.connect(self._on_connect_error)
        self._worker = w
        w.start()

    @pyqtSlot(object)
    def _on_connected(self, manager):
        self._manager = manager
        self.conn_lbl.setText(f"● 已连接:  {manager.adb.device_id}")
        self.conn_lbl.setStyleSheet(
            "color:#6fcf6f; font-weight:bold; font-size:11pt; background:transparent;"
        )
        self._set_busy(False)
        self.log.append_log("数据库拉取成功，可以开始操作。", "ok")

    @pyqtSlot(str)
    def _on_connect_error(self, msg):
        self.conn_lbl.setText("● 连接失败")
        self.conn_lbl.setStyleSheet(
            "color:#f07070; font-weight:bold; font-size:11pt; background:transparent;"
        )
        self._set_busy(False)
        self.log.append_log(msg, "error")

    # ── 分析 ────────────────────────────────────────────────

    @pyqtSlot(float)
    def _on_request_analyze(self, threshold: float):
        if threshold < 0:
            return
        if not self._manager:
            QMessageBox.warning(self, "未连接", "请先连接设备")
            return
        self._set_busy(True, f"正在分析（阈值 {threshold*100:.0f}%）…")
        self.log.append_log(f"\n=== 分析开始  阈值 {threshold*100:.0f}% ===", "info")

        w = AnalyzeWorker(self._manager, threshold)
        w.signals.log.connect(self.log.append_log)
        w.signals.finished.connect(self._on_analyze_done)
        w.signals.error.connect(
            lambda e: (self.log.append_log(e, "error"), self._set_busy(False))
        )
        self._worker = w
        w.start()

    @pyqtSlot(object)
    def _on_analyze_done(self, results):
        self._results = results
        self.move_tab.set_results(results)
        self.analyze_tab.populate(results)
        self._set_busy(False)
        self.log.append_log(f"分析完成: {len(results)} 个达标漫画", "ok")

    # ── 移动 ────────────────────────────────────────────────

    @pyqtSlot(list, str, bool, bool)
    def _on_request_move(self, items, dest, remove, sync):
        if not self._manager:
            return
        self._set_busy(True, f"正在移动 {len(items)} 个漫画…")
        self.log.append_log(f"\n=== 移动开始  {len(items)} 个 → {dest} ===", "info")

        w = MoveWorker(self._manager, items, dest, remove, sync)
        w.signals.log.connect(self.log.append_log)
        w.signals.finished.connect(self._on_move_done)
        w.signals.error.connect(
            lambda e: (self.log.append_log(e, "error"), self._set_busy(False))
        )
        self._worker = w
        w.start()

    @pyqtSlot(object)
    def _on_move_done(self, summary: dict):
        self._set_busy(False)
        moved = len(summary.get("moved", []))
        failed = len(summary.get("failed", []))
        msg = f"移动完成: 成功 {moved}，失败 {failed}"
        self.log.append_log(f"\n{msg}", "ok" if failed == 0 else "warn")
        QMessageBox.information(self, "完成", msg)
        moved_set = set(summary.get("moved", []))
        self._results = [r for r in self._results if r["gid"] not in moved_set]
        self.move_tab.set_results(self._results)
        self.analyze_tab.populate(self._results)

    # ── 文件名检查 ──────────────────────────────────────────

    def _on_check_names(self):
        if not self._manager:
            QMessageBox.warning(self, "未连接", "请先连接设备")
            return
        self.filename_tab.populate(self._manager.preview_filename_issues())

    # ── 辅助 ────────────────────────────────────────────────

    def _set_busy(self, busy: bool, msg: str = ""):
        self.progress_bar.setVisible(busy)
        self.reconnect_btn.setEnabled(not busy)
        self.move_tab.set_busy(busy)
        if msg:
            self.conn_lbl.setText(f"● {msg}")

    def closeEvent(self, event):
        if self._manager:
            self._manager.cleanup()
        event.accept()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_gui():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 全局字体
    app.setFont(QFont("Microsoft YaHei UI", 11))

    # 深色调色板
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(38, 38, 38))
    pal.setColor(QPalette.WindowText, QColor(220, 220, 220))
    pal.setColor(QPalette.Base, QColor(26, 26, 26))
    pal.setColor(QPalette.AlternateBase, QColor(34, 34, 34))
    pal.setColor(QPalette.Text, QColor(220, 220, 220))
    pal.setColor(QPalette.Button, QColor(52, 52, 52))
    pal.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    pal.setColor(QPalette.Highlight, QColor(50, 100, 170))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ToolTipBase, QColor(30, 45, 60))
    pal.setColor(QPalette.ToolTipText, QColor(210, 225, 240))
    pal.setColor(QPalette.Mid, QColor(55, 55, 55))
    pal.setColor(QPalette.Dark, QColor(20, 20, 20))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    return app.exec_()
