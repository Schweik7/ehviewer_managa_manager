"""
EhViewer漫画管理工具 - PyQt5 图形界面

主窗口包含四个选项卡:
  移动漫画 | 分析进度 | 清理记录 | 数据库统计
长时操作在 QThread 中运行, 避免冻结界面。
"""

import os
import sys
import traceback
from typing import Optional, List

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QObject
from PyQt5.QtGui import QFont, QTextCursor, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QTabWidget, QTextEdit, QFileDialog, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QSplitter, QFrame, QMessageBox,
)

from .config import DEFAULT_THRESHOLD
from .manager import MangaManager


# ---------------------------------------------------------------------------
# 工作线程基类
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    """工作线程信号集合"""
    log = pyqtSignal(str, str)      # (message, level) level: info/ok/warn/error
    finished = pyqtSignal(object)   # 返回结果
    error = pyqtSignal(str)


class BaseWorker(QThread):
    """所有后台任务的基类"""

    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self._manager: Optional[MangaManager] = None

    def _log(self, msg: str, level: str = "info"):
        self.signals.log.emit(msg, level)

    def _init_manager(self) -> bool:
        """初始化 MangaManager (连接设备、拉取数据库)。"""
        self._manager = MangaManager()
        # 重定向 print 到 GUI log
        import builtins
        orig_print = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            level = "info"
            if any(kw in msg for kw in ["失败", "错误", "Error", "error"]):
                level = "error"
            elif any(kw in msg for kw in ["警告", "Warning", "warn", "⚠"]):
                level = "warn"
            elif any(kw in msg for kw in ["✓", "完成", "成功", "已拉取", "已连接"]):
                level = "ok"
            self._log(msg, level)
            orig_print(*args, **kwargs)

        builtins.print = gui_print
        try:
            ok = self._manager.initialize()
        finally:
            builtins.print = orig_print
        return ok

    def _cleanup(self):
        if self._manager:
            self._manager.cleanup()


class ConnectWorker(BaseWorker):
    """检查设备连接 + 拉取数据库"""

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
    """分析阅读进度"""

    def __init__(self, manager: MangaManager, threshold: float):
        super().__init__()
        self._manager = manager
        self.threshold = threshold

    def _init_manager(self) -> bool:
        return True  # 已由主线程初始化

    def run(self):
        import builtins
        orig_print = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            level = "ok" if "[达标]" in msg else ("error" if "[失败]" in msg else "info")
            self._log(msg, level)
            orig_print(*args, **kwargs)

        builtins.print = gui_print
        try:
            results = self._manager.analyze_reading_progress(self.threshold)
            self.signals.finished.emit(results)
        except Exception as e:
            self.signals.error.emit(f"分析出错: {e}")
        finally:
            builtins.print = orig_print


class MoveWorker(BaseWorker):
    """执行漫画移动"""

    def __init__(self, manager: MangaManager, results: list,
                 dest_dir: str, remove: bool, sync_db: bool):
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
        orig_print = builtins.print

        def gui_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            level = "info"
            if "已拉取" in msg or "成功" in msg or "已删除" in msg:
                level = "ok"
            elif "失败" in msg or "错误" in msg:
                level = "error"
            elif "警告" in msg:
                level = "warn"
            self._log(msg, level)
            orig_print(*args, **kwargs)

        builtins.print = gui_print
        try:
            os.makedirs(self.dest_dir, exist_ok=True)
            moved_gids = []
            failed_titles = []

            for manga in self.results:
                ok = self._manager.move_manga_to_pc(
                    manga, self.dest_dir,
                    remove_from_phone=self.remove,
                    dry_run=False,
                )
                if ok:
                    moved_gids.append(manga["gid"])
                else:
                    failed_titles.append(manga["title"])

            summary = {"moved": moved_gids, "failed": failed_titles}

            if self.sync_db and moved_gids:
                self._log(f"\n正在清理 {len(moved_gids)} 条数据库记录...", "info")
                deleted = self._manager.clean_database_records(moved_gids)
                self._log(f"已清理 {deleted}/{len(moved_gids)} 条记录", "ok")
                self._manager.create_backup_and_push()

            self.signals.finished.emit(summary)
        except Exception as e:
            self.signals.error.emit(f"移动出错: {e}\n{traceback.format_exc()}")
        finally:
            builtins.print = orig_print


# ---------------------------------------------------------------------------
# 日志显示组件
# ---------------------------------------------------------------------------

class LogWidget(QTextEdit):
    """带颜色的日志输出框"""

    COLORS = {
        "info":  "#e0e0e0",
        "ok":    "#6fcf6f",
        "warn":  "#f0c040",
        "error": "#f07070",
    }

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet("background-color: #1e1e1e; color: #e0e0e0; border: 1px solid #444;")

    @pyqtSlot(str, str)
    def append_log(self, msg: str, level: str = "info"):
        color = self.COLORS.get(level, self.COLORS["info"])
        escaped = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.append(f'<span style="color:{color};">{escaped}</span>')
        self.moveCursor(QTextCursor.End)

    def clear_log(self):
        self.clear()


# ---------------------------------------------------------------------------
# 选项卡: 移动漫画
# ---------------------------------------------------------------------------

class MoveTab(QWidget):
    request_analyze = pyqtSignal(float)     # 触发分析
    request_move    = pyqtSignal(list, str, bool, bool)  # (results, dest, remove, sync)

    def __init__(self):
        super().__init__()
        self._analyze_results: List[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 目标目录
        dest_group = QGroupBox("目标目录")
        dlay = QHBoxLayout(dest_group)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("选择或输入本地存储路径...")
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_dest)
        dlay.addWidget(self.dest_edit)
        dlay.addWidget(browse_btn)
        layout.addWidget(dest_group)

        # 选项
        opt_group = QGroupBox("选项")
        olay = QHBoxLayout(opt_group)

        thr_lbl = QLabel("阅读进度阈值:")
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.1, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(DEFAULT_THRESHOLD)
        self.threshold_spin.setDecimals(2)

        batch_lbl = QLabel("批次大小(0=全部):")
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(0, 9999)
        self.batch_spin.setValue(0)
        self.batch_spin.setSpecialValueText("全部")

        self.remove_chk = QCheckBox("移动后删除手机原文件")
        self.sync_chk   = QCheckBox("同步更新EhViewer数据库")

        olay.addWidget(thr_lbl)
        olay.addWidget(self.threshold_spin)
        olay.addSpacing(20)
        olay.addWidget(batch_lbl)
        olay.addWidget(self.batch_spin)
        olay.addSpacing(20)
        olay.addWidget(self.remove_chk)
        olay.addWidget(self.sync_chk)
        olay.addStretch()
        layout.addWidget(opt_group)

        # 分析结果摘要
        self.summary_lbl = QLabel("尚未分析。请先点击「分析」获取待移动漫画列表。")
        self.summary_lbl.setStyleSheet("color: #aaa; padding: 6px;")
        layout.addWidget(self.summary_lbl)

        # 按钮行
        btn_layout = QHBoxLayout()
        self.analyze_btn = QPushButton("① 分析阅读进度")
        self.analyze_btn.setMinimumHeight(36)
        self.analyze_btn.clicked.connect(self._on_analyze)

        self.dryrun_btn = QPushButton("② 预演 (Dry Run)")
        self.dryrun_btn.setMinimumHeight(36)
        self.dryrun_btn.setEnabled(False)
        self.dryrun_btn.clicked.connect(self._on_dryrun)

        self.move_btn = QPushButton("③ 执行移动")
        self.move_btn.setMinimumHeight(36)
        self.move_btn.setEnabled(False)
        self.move_btn.setStyleSheet("QPushButton { background-color: #2e5a2e; color: white; }"
                                    "QPushButton:hover { background-color: #3a7a3a; }"
                                    "QPushButton:disabled { background-color: #444; color: #888; }")
        self.move_btn.clicked.connect(self._on_move)

        btn_layout.addWidget(self.analyze_btn)
        btn_layout.addWidget(self.dryrun_btn)
        btn_layout.addWidget(self.move_btn)
        layout.addLayout(btn_layout)
        layout.addStretch()

    def _browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "选择目标目录")
        if folder:
            self.dest_edit.setText(folder)

    def _on_analyze(self):
        self.request_analyze.emit(self.threshold_spin.value())

    def _on_dryrun(self):
        if not self._analyze_results:
            return
        dest = self.dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "提示", "请先选择目标目录")
            return
        batch = self.batch_spin.value()
        items = self._analyze_results[:batch] if batch > 0 else self._analyze_results
        # 仅显示预演摘要，不实际操作
        msgs = [f"[DRY-RUN] 将移动 {len(items)} 个漫画到: {dest}"]
        for m in items:
            msgs.append(f"  {m['title'][:60]}  →  {dest}")
        self.request_analyze.emit(-1)  # 信号触发日志显示
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTextEdit as TE
        dlg = QDialog(self)
        dlg.setWindowTitle("Dry Run 预览")
        dlg.resize(700, 400)
        vl = QVBoxLayout(dlg)
        te = TE()
        te.setReadOnly(True)
        te.setPlainText("\n".join(msgs))
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
        if not self._analyze_results:
            QMessageBox.warning(self, "提示", "请先执行分析")
            return

        batch = self.batch_spin.value()
        items = self._analyze_results[:batch] if batch > 0 else self._analyze_results

        confirm = QMessageBox.question(
            self, "确认",
            f"将移动 {len(items)} 个漫画到:\n{dest}\n\n"
            f"删除手机原文件: {'是' if self.remove_chk.isChecked() else '否'}\n"
            f"同步数据库: {'是' if self.sync_chk.isChecked() else '否'}\n\n继续?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.request_move.emit(
            items, dest,
            self.remove_chk.isChecked(),
            self.sync_chk.isChecked(),
        )

    def set_analyze_results(self, results: List[dict]):
        self._analyze_results = results
        n = len(results)
        if n:
            self.summary_lbl.setText(
                f"找到 {n} 个达标漫画。可使用批次大小限制每次移动数量。"
            )
            self.summary_lbl.setStyleSheet("color: #6fcf6f; padding: 6px;")
        else:
            self.summary_lbl.setText("未找到达到阈值的漫画。")
            self.summary_lbl.setStyleSheet("color: #f0c040; padding: 6px;")
        self.dryrun_btn.setEnabled(n > 0)
        self.move_btn.setEnabled(n > 0)

    def set_busy(self, busy: bool):
        for w in (self.analyze_btn, self.dryrun_btn, self.move_btn):
            w.setEnabled(not busy)
        if not busy and self._analyze_results:
            self.dryrun_btn.setEnabled(True)
            self.move_btn.setEnabled(True)


# ---------------------------------------------------------------------------
# 选项卡: 分析结果表格
# ---------------------------------------------------------------------------

class AnalyzeTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["GID", "标题", "进度", "页数", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("分析结果 (仅展示达标漫画):"))
        layout.addWidget(self.table)

    def populate(self, results: List[dict]):
        self.table.setRowCount(0)
        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(r["gid"])))
            self.table.setItem(row, 1, QTableWidgetItem(r["title"]))
            self.table.setItem(row, 2, QTableWidgetItem(f"{r['progress']*100:.1f}%"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{r['current_page']+1}/{r['total_pages']}"))
            self.table.setItem(row, 4, QTableWidgetItem(r["state_text"]))


# ---------------------------------------------------------------------------
# 选项卡: 文件名检查
# ---------------------------------------------------------------------------

class FilenameTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.label = QLabel("点击「检查文件名」扫描数据库中的 Windows 非法字符")
        layout.addWidget(self.label)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["GID", "原始目录名", "净化后目录名"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

    def populate(self, issues: List[dict]):
        self.table.setRowCount(0)
        if not issues:
            self.label.setText("✓ 所有目录名均兼容 Windows，无需净化。")
            return
        self.label.setText(f"发现 {len(issues)} 个目录名需要净化（移动时自动处理）:")
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
        self.resize(1100, 700)
        self._manager: Optional[MangaManager] = None
        self._worker: Optional[QThread] = None
        self._analyze_results: List[dict] = []
        self._build_ui()
        self._auto_connect()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # 顶部状态栏
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.StyledPanel)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(8, 4, 8, 4)

        self.conn_lbl = QLabel("● 未连接")
        self.conn_lbl.setStyleSheet("color: #f07070; font-weight: bold;")
        self.reconnect_btn = QPushButton("重新连接")
        self.reconnect_btn.setFixedWidth(90)
        self.reconnect_btn.clicked.connect(self._auto_connect)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 不定进度
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(120)

        status_layout.addWidget(self.conn_lbl)
        status_layout.addWidget(self.reconnect_btn)
        status_layout.addStretch()
        status_layout.addWidget(self.progress_bar)
        main_layout.addWidget(status_frame)

        # 主区域: 左选项卡 + 右日志
        splitter = QSplitter(Qt.Horizontal)

        # 左: 选项卡
        tabs = QTabWidget()

        self.move_tab = MoveTab()
        self.move_tab.request_analyze.connect(self._on_request_analyze)
        self.move_tab.request_move.connect(self._on_request_move)
        tabs.addTab(self.move_tab, "移动漫画")

        self.analyze_tab = AnalyzeTab()
        tabs.addTab(self.analyze_tab, "分析结果")

        self.filename_tab = FilenameTab()
        fn_btn = QPushButton("检查文件名兼容性")
        fn_btn.clicked.connect(self._on_check_names)
        fn_layout = self.filename_tab.layout()
        fn_layout.insertWidget(1, fn_btn)
        tabs.addTab(self.filename_tab, "文件名检查")

        splitter.addWidget(tabs)

        # 右: 日志
        log_frame = QWidget()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_hdr = QHBoxLayout()
        log_hdr.addWidget(QLabel("运行日志"))
        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(50)
        clear_btn.clicked.connect(lambda: self.log.clear_log())
        log_hdr.addWidget(clear_btn)
        self.log = LogWidget()
        log_layout.addLayout(log_hdr)
        log_layout.addWidget(self.log)
        splitter.addWidget(log_frame)

        splitter.setSizes([600, 480])
        main_layout.addWidget(splitter)

    # ------------------------------------------------------------------
    # 连接 / 初始化
    # ------------------------------------------------------------------

    def _auto_connect(self):
        self._set_busy(True, "正在连接设备并拉取数据库...")
        self.conn_lbl.setText("● 连接中...")
        self.conn_lbl.setStyleSheet("color: #f0c040; font-weight: bold;")
        if self._manager:
            self._manager.cleanup()
            self._manager = None

        worker = ConnectWorker()
        worker.signals.log.connect(self.log.append_log)
        worker.signals.finished.connect(self._on_connected)
        worker.signals.error.connect(self._on_connect_error)
        self._worker = worker
        worker.start()

    @pyqtSlot(object)
    def _on_connected(self, manager):
        self._manager = manager
        self.conn_lbl.setText(f"● 已连接: {manager.adb.device_id}")
        self.conn_lbl.setStyleSheet("color: #6fcf6f; font-weight: bold;")
        self._set_busy(False)
        self.log.append_log("数据库拉取成功，可以开始操作。", "ok")

    @pyqtSlot(str)
    def _on_connect_error(self, msg):
        self.conn_lbl.setText("● 连接失败")
        self.conn_lbl.setStyleSheet("color: #f07070; font-weight: bold;")
        self._set_busy(False)
        self.log.append_log(msg, "error")

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------

    @pyqtSlot(float)
    def _on_request_analyze(self, threshold: float):
        if threshold < 0:
            return  # dry-run信号, 忽略
        if not self._manager:
            QMessageBox.warning(self, "未连接", "请先连接设备")
            return
        self._set_busy(True, f"正在分析 (阈值 {threshold*100:.0f}%)...")
        self.log.append_log(f"\n=== 开始分析 阈值={threshold*100:.0f}% ===", "info")

        worker = AnalyzeWorker(self._manager, threshold)
        worker.signals.log.connect(self.log.append_log)
        worker.signals.finished.connect(self._on_analyze_done)
        worker.signals.error.connect(lambda e: (self.log.append_log(e, "error"), self._set_busy(False)))
        self._worker = worker
        worker.start()

    @pyqtSlot(object)
    def _on_analyze_done(self, results):
        self._analyze_results = results
        self.move_tab.set_analyze_results(results)
        self.analyze_tab.populate(results)
        self._set_busy(False)
        self.log.append_log(f"\n分析完成: {len(results)} 个达标漫画", "ok")

    # ------------------------------------------------------------------
    # 移动
    # ------------------------------------------------------------------

    @pyqtSlot(list, str, bool, bool)
    def _on_request_move(self, items: list, dest: str, remove: bool, sync: bool):
        if not self._manager:
            return
        self._set_busy(True, f"正在移动 {len(items)} 个漫画...")
        self.log.append_log(f"\n=== 开始移动 {len(items)} 个漫画 → {dest} ===", "info")

        worker = MoveWorker(self._manager, items, dest, remove, sync)
        worker.signals.log.connect(self.log.append_log)
        worker.signals.finished.connect(self._on_move_done)
        worker.signals.error.connect(lambda e: (self.log.append_log(e, "error"), self._set_busy(False)))
        self._worker = worker
        worker.start()

    @pyqtSlot(object)
    def _on_move_done(self, summary: dict):
        self._set_busy(False)
        moved = len(summary.get("moved", []))
        failed = len(summary.get("failed", []))
        msg = f"移动完成: 成功 {moved}, 失败 {failed}"
        level = "ok" if failed == 0 else "warn"
        self.log.append_log(f"\n{msg}", level)
        QMessageBox.information(self, "完成", msg)
        # 重新分析以更新列表
        self._analyze_results = [
            r for r in self._analyze_results
            if r["gid"] not in summary.get("moved", [])
        ]
        self.move_tab.set_analyze_results(self._analyze_results)
        self.analyze_tab.populate(self._analyze_results)

    # ------------------------------------------------------------------
    # 文件名检查
    # ------------------------------------------------------------------

    def _on_check_names(self):
        if not self._manager:
            QMessageBox.warning(self, "未连接", "请先连接设备")
            return
        issues = self._manager.preview_filename_issues()
        self.filename_tab.populate(issues)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

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

    # 深色主题
    from PyQt5.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
    palette.setColor(QPalette.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase, QColor(40, 40, 40))
    palette.setColor(QPalette.Text, QColor(224, 224, 224))
    palette.setColor(QPalette.Button, QColor(55, 55, 55))
    palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))
    palette.setColor(QPalette.Highlight, QColor(60, 100, 160))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    return app.exec_()
