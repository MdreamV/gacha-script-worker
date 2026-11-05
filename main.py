from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

try:
    import pystray
except ImportError:  # 托盘不可用时仍可运行主界面
    pystray = None

import automation
import config as appconfig

APP_NAME = "GachaScriptWorker"
APP_TITLE = f"{APP_NAME} · 二游脚本总控"
TRAY_TITLE = "GachaWorker"
MUTEX_NAME = "Local\\GachaScriptWorker_v1"


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def is_instance_running() -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def show_already_running() -> None:
    temp = tk.Tk()
    temp.withdraw()
    messagebox.showinfo(APP_TITLE, "程序已经在运行，请查看右下角系统托盘。", parent=temp)
    temp.destroy()


def relaunch_as_admin() -> bool:
    if getattr(sys, "frozen", False):
        params = ""
    else:
        params = f'"{Path(__file__).resolve()}"'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, str(appconfig.APP_DIR), 1
        )
        return int(result) > 32
    except Exception:
        return False


def handle_elevation() -> bool:
    """返回 True 表示当前进程应退出（已发起管理员重启，或用户选择退出）。"""
    if automation.is_elevated():
        return False
    temp = tk.Tk()
    temp.withdraw()
    choice = messagebox.askyesnocancel(
        APP_TITLE,
        "当前未以管理员身份运行。\n\n"
        "三月七小助手和绝区零一条龙的启动程序都要求管理员权限，"
        "普通权限下无法启动脚本，定时运行也无法正常工作。\n\n"
        "是：以管理员身份重新启动（推荐，只需确认一次 UAC）\n"
        "否：以普通权限继续（可以查看界面，但无法启动脚本）\n"
        "取消：退出程序",
        parent=temp,
    )
    temp.destroy()
    if choice is None:
        return True
    if not choice:
        return False
    if relaunch_as_admin():
        return True
    temp = tk.Tk()
    temp.withdraw()
    messagebox.showwarning(
        APP_TITLE,
        "未获得管理员授权（可能取消了 UAC），将以普通权限继续运行。\n两个脚本都将无法启动。",
        parent=temp,
    )
    temp.destroy()
    return False


def make_icon_image(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (1, 1, size - 2, size - 2),
        radius=max(6, int(size * 0.24)),
        fill=(31, 51, 108, 255),
    )
    draw.rounded_rectangle(
        (int(size * 0.09), int(size * 0.09), int(size * 0.91), int(size * 0.91)),
        radius=max(4, int(size * 0.18)),
        outline=(108, 160, 255, 190),
        width=max(1, size // 32),
    )
    draw.polygon(
        [
            (size * 0.40, size * 0.29),
            (size * 0.40, size * 0.71),
            (size * 0.72, size * 0.50),
        ],
        fill=(255, 255, 255, 255),
    )
    return img


def acquire_instance_lock():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


class WorkerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.run_thread: threading.Thread | None = None
        self.running = False
        self.records: list[dict] = []
        self.latest: dict[str, dict] = {}
        self.next_run: datetime | None = None
        self.tray = None
        self.tray_ok = False
        self.hide_notified = False

        self.cfg = appconfig.load_config()
        self.state = appconfig.load_state()

        self.schedule_enabled = tk.BooleanVar(value=self.cfg["schedule"]["enabled"])
        self.schedule_time = tk.StringVar(value=self.cfg["schedule"]["time"])
        self.status_var = tk.StringVar(value="空闲")
        self.next_var = tk.StringVar(value="下次自动启动：-")

        self.scripts = [
            {
                "id": item["id"],
                "name": item["name"],
                "enabled": tk.BooleanVar(value=item["enabled"]),
                "path": tk.StringVar(value=item["path"]),
                "widgets": [],
            }
            for item in self.cfg["scripts"]
        ]

        appconfig.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._build_ui()
        self._load_history()
        self._refresh_status_tree()
        self._setup_tray()
        self._recompute_next()
        self.ui_log(f"{APP_TITLE} 已启动")
        if automation.is_elevated():
            self.ui_log("当前权限：管理员（可正常启动并控制全部脚本）")
        else:
            self.ui_log(
                "当前权限：普通用户。三月七和一条龙都要求管理员权限，无法启动；米游社签到不受影响。"
                "请退出后右键“以管理员身份运行”。"
            )
        self.ui_log(f"日志目录：{appconfig.LOG_DIR}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.after(120, self._poll_queue)
        self.root.after(3000, self._schedule_tick)

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1120, screen_w - 80)
        height = min(860, screen_h - 80)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(900, 640)

        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        default_font = ("Microsoft YaHei UI", 9)
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=9)
        tkfont.nametofont("TkTextFont").configure(family="Microsoft YaHei UI", size=9)
        style = ttk.Style()
        style.configure("Treeview", rowheight=25, font=default_font)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

        self.icon_image = make_icon_image(64)
        self._icon_photo = ImageTk.PhotoImage(self.icon_image)
        self.root.iconphoto(True, self._icon_photo)

        bar = ttk.Frame(self.root, padding=(12, 4))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, text="当前状态：").pack(side="left")
        ttk.Label(bar, textvariable=self.status_var, foreground="#0b5cad").pack(side="left")
        ttk.Label(
            bar,
            text="关闭或最小化窗口会收起到右下角托盘，双击托盘图标可重新打开",
            foreground="#888888",
        ).pack(side="right")

        outer = ttk.Frame(self.root, padding=(12, 10, 12, 8))
        outer.pack(fill="both", expand=True)

        # ---- 定时启动
        sched = ttk.LabelFrame(outer, text="定时启动", padding=(10, 6))
        sched.pack(fill="x", pady=(0, 8))
        self.schedule_check = ttk.Checkbutton(
            sched,
            text="启用每日定时启动",
            variable=self.schedule_enabled,
            command=self._on_schedule_setting_changed,
        )
        self.schedule_check.grid(row=0, column=0, sticky="w")
        ttk.Label(sched, text="启动时间").grid(row=0, column=1, padx=(20, 4))
        self.time_entry = ttk.Entry(sched, textvariable=self.schedule_time, width=8)
        self.time_entry.grid(row=0, column=2, sticky="w")
        ttk.Label(sched, text="（24 小时制，例如 08:20）", foreground="#777777").grid(
            row=0, column=3, padx=(6, 0), sticky="w"
        )
        ttk.Label(sched, textvariable=self.next_var, foreground="#0b5cad").grid(
            row=0, column=4, padx=(28, 0), sticky="w"
        )
        sched.columnconfigure(4, weight=1)
        self.schedule_time.trace_add("write", lambda *_: self._on_schedule_setting_changed())

        # ---- 脚本设置
        box = ttk.LabelFrame(outer, text="脚本设置（按从上到下的顺序依次执行）", padding=(10, 8))
        box.pack(fill="x", pady=(0, 8))
        header = ttk.Frame(box)
        header.pack(fill="x")
        ttk.Label(header, text="顺序", width=8).pack(side="left")
        ttk.Label(header, text="启用", width=6).pack(side="left")
        ttk.Label(header, text="脚本", width=16).pack(side="left")
        ttk.Label(header, text="启动程序 / 脚本目录").pack(side="left", padx=(4, 0))
        self.rows_frame = ttk.Frame(box)
        self.rows_frame.pack(fill="x", pady=(4, 0))
        self._rebuild_script_rows()

        # ---- 操作按钮
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 8))
        self.run_btn = ttk.Button(actions, text="立即运行全部", command=self.start_run_manual)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="停止任务", command=self.stop_run, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.exit_btn = ttk.Button(actions, text="退出程序", command=self.request_quit)
        self.exit_btn.pack(side="right")
        self.log_btn = ttk.Button(actions, text="打开日志目录", command=self._open_log_dir)
        self.log_btn.pack(side="right", padx=(0, 8))
        self.save_btn = ttk.Button(actions, text="保存配置", command=self._save_config_clicked)
        self.save_btn.pack(side="right", padx=(0, 8))

        # ---- 日志（固定在底部）
        log_box = ttk.LabelFrame(outer, text="运行日志", padding=(6, 4))
        log_box.pack(side="bottom", fill="x", pady=(8, 0))
        self.log_text = tk.Text(
            log_box,
            height=9,
            wrap="word",
            state="disabled",
            font=default_font,
            background="#fbfbfb",
            relief="solid",
            borderwidth=1,
        )
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # ---- 状态 / 历史
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        status_tab = ttk.Frame(notebook, padding=(4, 6))
        notebook.add(status_tab, text="执行状态")
        status_cols = ("script", "state", "start", "launched", "completed", "end", "duration", "note")
        self.status_tree = ttk.Treeview(
            status_tab, columns=status_cols, show="headings", selectmode="none"
        )
        for col, text, width in (
            ("script", "脚本", 110),
            ("state", "状态", 100),
            ("start", "开始时间", 140),
            ("launched", "启动", 55),
            ("completed", "完成", 55),
            ("end", "结束时间", 140),
            ("duration", "用时", 85),
            ("note", "说明", 380),
        ):
            self.status_tree.heading(col, text=text)
            anchor = "center" if col in ("launched", "completed") else "w"
            self.status_tree.column(col, width=width, anchor=anchor, stretch=(col == "note"))
        status_scroll = ttk.Scrollbar(status_tab, orient="vertical", command=self.status_tree.yview)
        status_xscroll = ttk.Scrollbar(
            status_tab, orient="horizontal", command=self.status_tree.xview
        )
        self.status_tree.configure(
            yscrollcommand=status_scroll.set, xscrollcommand=status_xscroll.set
        )
        status_xscroll.pack(side="bottom", fill="x")
        self.status_tree.pack(side="left", fill="both", expand=True)
        status_scroll.pack(side="right", fill="y")

        history_tab = ttk.Frame(notebook, padding=(4, 6))
        notebook.add(history_tab, text="运行历史")
        history_cols = ("script", "state", "start", "end", "duration", "launched", "completed", "note")
        self.history_tree = ttk.Treeview(
            history_tab, columns=history_cols, show="headings", selectmode="browse"
        )
        for col, text, width in (
            ("script", "脚本", 110),
            ("state", "状态", 90),
            ("start", "开始时间", 140),
            ("end", "结束时间", 140),
            ("duration", "用时", 85),
            ("launched", "启动", 55),
            ("completed", "完成", 55),
            ("note", "说明", 380),
        ):
            self.history_tree.heading(col, text=text)
            anchor = "center" if col in ("launched", "completed") else "w"
            self.history_tree.column(col, width=width, anchor=anchor, stretch=(col == "note"))
        history_scroll = ttk.Scrollbar(history_tab, orient="vertical", command=self.history_tree.yview)
        history_xscroll = ttk.Scrollbar(
            history_tab, orient="horizontal", command=self.history_tree.xview
        )
        self.history_tree.configure(
            yscrollcommand=history_scroll.set, xscrollcommand=history_xscroll.set
        )
        history_xscroll.pack(side="bottom", fill="x")
        self.history_tree.pack(side="left", fill="both", expand=True)
        history_scroll.pack(side="right", fill="y")

        for tree in (self.status_tree, self.history_tree):
            tree.tag_configure("ok", foreground="#177245")
            tree.tag_configure("fail", foreground="#b00020")
            tree.tag_configure("run", foreground="#0b5cad")
            tree.tag_configure("warn", foreground="#a15c00")
            tree.tag_configure("idle", foreground="#777777")

    def _rebuild_script_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        for index, script in enumerate(self.scripts):
            row = ttk.Frame(self.rows_frame)
            row.grid(row=index, column=0, sticky="ew", pady=2)
            self.rows_frame.columnconfigure(0, weight=1)

            order_box = ttk.Frame(row)
            order_box.pack(side="left")
            up_btn = ttk.Button(
                order_box, text="▲", width=3, command=lambda i=index: self._move_script(i, -1)
            )
            up_btn.pack(side="left")
            down_btn = ttk.Button(
                order_box, text="▼", width=3, command=lambda i=index: self._move_script(i, 1)
            )
            down_btn.pack(side="left", padx=(2, 0))

            chk = ttk.Checkbutton(row, variable=script["enabled"], text="启用")
            chk.pack(side="left", padx=(10, 0))
            ttk.Label(row, text=script["name"], width=14, font=("Microsoft YaHei UI", 9, "bold")).pack(
                side="left", padx=(8, 0)
            )
            browse_btn = ttk.Button(row, text="浏览…", width=8, command=lambda s=script: self._browse(s))
            browse_btn.pack(side="right")
            open_btn = ttk.Button(row, text="打开", width=6, command=lambda s=script: self._open_script(s))
            open_btn.pack(side="right", padx=(0, 4))
            entry = ttk.Entry(row, textvariable=script["path"])
            entry.pack(side="left", fill="x", expand=True, padx=(4, 6))

            script["widgets"] = [up_btn, down_btn, chk, entry, browse_btn, open_btn]
            if self.running:
                for widget in script["widgets"] + [self.time_entry, self.schedule_check]:
                    try:
                        widget.configure(state="disabled")
                    except tk.TclError:
                        pass
        if hasattr(self, "status_tree"):
            self._refresh_status_tree()

    def _move_script(self, index: int, delta: int) -> None:
        new_index = index + delta
        if not (0 <= new_index < len(self.scripts)):
            return
        self.scripts[index], self.scripts[new_index] = self.scripts[new_index], self.scripts[index]
        self._rebuild_script_rows()

    def _open_script(self, script: dict) -> None:
        path = script["path"].get().strip().strip('"')
        try:
            automation.open_script_manually(script["id"], path)
        except automation.AutomationError as exc:
            messagebox.showerror("无法打开脚本", str(exc), parent=self.root)
            self.ui_log(f"打开 {script['name']} 失败：{exc}")
            return
        self.ui_log(f"已手动打开 {script['name']}")

    def _browse(self, script: dict) -> None:
        current = appconfig.resolve_path(script["path"].get())
        if appconfig.SCRIPT_KINDS.get(script["id"]) == "console":
            initial = current if current and os.path.isdir(current) else str(appconfig.APP_DIR)
            path = filedialog.askdirectory(
                parent=self.root,
                title=f"选择 {script['name']} 的脚本文件夹",
                initialdir=initial,
                mustexist=True,
            )
        else:
            initial = os.path.dirname(current) if current and os.path.isdir(os.path.dirname(current)) else str(appconfig.APP_DIR)
            path = filedialog.askopenfilename(
                parent=self.root,
                title=f"选择 {script['name']} 的启动程序",
                initialdir=initial,
                filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
            )
        if path:
            script["path"].set(os.path.normpath(path))

    # ------------------------------------------------------------ 日志 / 状态

    def _log_file_path(self):
        return appconfig.LOG_DIR / f"worker_{datetime.now():%Y%m%d}.log"

    def ui_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        if int(self.log_text.index("end-1c").split(".")[0]) > 4000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.configure(state="disabled")
        try:
            appconfig.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._log_file_path(), "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
        except OSError:
            pass

    @staticmethod
    def _bool_text(value) -> str:
        if value is True:
            return "是"
        if value is False:
            return "否"
        return "—"

    @staticmethod
    def _duration_text(value) -> str:
        try:
            seconds = float(value or 0)
        except (TypeError, ValueError):
            return "—"
        if seconds <= 0:
            return "—"
        return automation.fmt_duration(seconds)

    @staticmethod
    def _tag_for(state: str) -> str:
        if state == "失败":
            return "fail"
        if state == "已完成":
            return "ok"
        if state in ("已停止", "未完成"):
            return "warn"
        if state in (
            "准备中",
            "启动脚本",
            "等待执行完成",
            "等待游戏启动",
            "游戏运行中",
            "MAA 运行中",
            "等待脚本窗口",
            "点击开始",
        ):
            return "run"
        return "idle"

    def _record_values(self, rec: dict | None, name: str) -> tuple:
        if not rec:
            return (name, "未运行", "—", "—", "—", "—", "—", "")
        return (
            rec.get("name") or name,
            rec.get("state") or "未运行",
            rec.get("start") or "—",
            self._bool_text(rec.get("launched")),
            self._bool_text(rec.get("completed")),
            rec.get("end") or "—",
            self._duration_text(rec.get("duration")),
            rec.get("note") or "",
        )

    def _refresh_status_tree(self) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)
        for script in self.scripts:
            rec = self.latest.get(script["id"])
            tag = self._tag_for((rec or {}).get("state", ""))
            self.status_tree.insert(
                "", "end", iid=script["id"], values=self._record_values(rec, script["name"]), tags=(tag,)
            )

    def _apply_record(self, rec: dict) -> None:
        script_id = rec.get("id")
        if not script_id:
            return
        self.latest[script_id] = rec
        self._refresh_status_tree()
        if rec.get("final"):
            clean = {
                key: value
                for key, value in rec.items()
                if key not in ("final",) and not key.startswith("_")
            }
            self.records.insert(0, clean)
            del self.records[100:]
            appconfig.save_state({"records": self.records})
            self._insert_history_row(clean)

    def _insert_history_row(self, rec: dict) -> None:
        tag = self._tag_for(rec.get("state", ""))
        values = (
            rec.get("name", ""),
            rec.get("state", ""),
            rec.get("start") or "—",
            rec.get("end") or "—",
            self._duration_text(rec.get("duration")),
            self._bool_text(rec.get("launched")),
            self._bool_text(rec.get("completed")),
            rec.get("note") or "",
        )
        self.history_tree.insert("", 0, values=values, tags=(tag,))

    def _load_history(self) -> None:
        self.records = list(self.state.get("records", []))
        for rec in reversed(self.records):
            self._insert_history_row(rec)
        for rec in self.records:
            script_id = rec.get("id")
            if script_id and script_id not in self.latest:
                self.latest[script_id] = rec

    # ------------------------------------------------------------ 定时启动

    @staticmethod
    def _parse_time(text: str) -> tuple[int, int]:
        parts = text.strip().split(":")
        if len(parts) != 2:
            raise ValueError("时间格式应为 HH:MM")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("时间超出范围")
        return hour, minute

    def _on_schedule_setting_changed(self) -> None:
        self._recompute_next()

    def _recompute_next(self) -> None:
        if not self.schedule_enabled.get():
            self.next_run = None
            self.next_var.set("下次自动启动：未启用")
            return
        try:
            hour, minute = self._parse_time(self.schedule_time.get())
        except (ValueError, TypeError):
            self.next_run = None
            self.next_var.set("下次自动启动：时间格式无效")
            return
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        self.next_run = target
        self.next_var.set("下次自动启动：" + target.strftime("%Y-%m-%d %H:%M"))

    def _schedule_tick(self) -> None:
        try:
            if (
                self.schedule_enabled.get()
                and self.next_run is not None
                and datetime.now() >= self.next_run
            ):
                if self.running:
                    self.ui_log("定时启动时间已到，但任务正在运行，本次跳过")
                else:
                    self.ui_log("定时启动时间已到，开始执行任务")
                    self._start_run(manual=False)
                self._recompute_next()
        finally:
            self.root.after(3000, self._schedule_tick)

    # ------------------------------------------------------------ 运行控制

    def _collect_scripts(self) -> list[dict]:
        return [
            {
                "id": script["id"],
                "name": script["name"],
                "path": script["path"].get().strip(),
            }
            for script in self.scripts
            if script["enabled"].get()
        ]

    @staticmethod
    def _script_path_ok(script: dict) -> bool:
        path = appconfig.resolve_path(script["path"])
        if appconfig.SCRIPT_KINDS.get(script["id"]) == "console":
            return os.path.isdir(path)
        return os.path.isfile(path)

    def start_run_manual(self) -> None:
        self._start_run(manual=True)

    def _start_run(self, manual: bool) -> None:
        if self.running:
            if manual:
                self.ui_log("任务已在运行中")
            return
        if not self._save_config(show_error=manual):
            return
        scripts = self._collect_scripts()
        if not scripts:
            self.ui_log("没有启用任何脚本，未执行")
            if manual:
                messagebox.showwarning("没有可执行脚本", "请至少启用一个脚本。", parent=self.root)
            return
        if manual:
            missing = [s for s in scripts if not self._script_path_ok(s)]
            if missing:
                detail = "\n".join(f"{s['name']}：{s['path']}" for s in missing)
                messagebox.showerror(
                    "脚本路径无效",
                    "以下路径不存在，请检查：\n\n" + detail,
                    parent=self.root,
                )
                return

        self.running = True
        self.stop_event.clear()
        self._set_running_ui(True)
        self.status_var.set("正在启动…")
        self.ui_log("=" * 40)
        self.ui_log("开始执行任务（手动）" if manual else "开始执行任务（定时）")
        self.run_thread = threading.Thread(
            target=self._run_worker, args=(scripts,), daemon=True
        )
        self.run_thread.start()

    def _run_worker(self, scripts: list[dict]) -> None:
        ctx = automation.RunContext(
            log=lambda message: self.q.put(("log", message)),
            set_status=lambda text: self.q.put(("status", text)),
            on_record=lambda rec, final=False: self.q.put(("record", rec)),
            stop_event=self.stop_event,
        )
        try:
            automation.run_sequence(ctx, scripts)
        except automation.StopRequested:
            self.q.put(("log", "任务已停止"))
        except Exception:
            self.q.put(("log", "运行出现异常：\n" + traceback.format_exc()))
        finally:
            self.q.put(("run_done", None))

    def _set_running_ui(self, running: bool) -> None:
        run_state = "disabled" if running else "normal"
        stop_state = "normal" if running else "disabled"
        for widget in (self.run_btn, self.save_btn, self.time_entry, self.schedule_check):
            try:
                widget.configure(state=run_state)
            except tk.TclError:
                pass
        self.stop_btn.configure(state=stop_state)
        for script in self.scripts:
            for widget in script["widgets"]:
                try:
                    widget.configure(state=run_state)
                except tk.TclError:
                    pass

    def stop_run(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("正在停止，请稍候…")
        self.ui_log("已发出停止指令")

    def _on_run_done(self) -> None:
        self.running = False
        self._set_running_ui(False)
        self.status_var.set("空闲")
        if self.tray_ok and not self.root.winfo_viewable():
            try:
                self.tray.notify("全部任务执行结束", APP_TITLE)
            except Exception:
                pass

    # ------------------------------------------------------------ 配置

    def _save_config(self, show_error: bool = True) -> bool:
        time_text = self.schedule_time.get().strip()
        try:
            hour, minute = self._parse_time(time_text)
        except (ValueError, TypeError):
            if show_error:
                messagebox.showerror(
                    "时间格式错误",
                    "定时启动时间格式应为 HH:MM，例如 08:20。",
                    parent=self.root,
                )
            return False
        normalized = f"{hour:02d}:{minute:02d}"
        if self.schedule_time.get() != normalized:
            self.schedule_time.set(normalized)
        cfg = {
            "schedule": {
                "enabled": bool(self.schedule_enabled.get()),
                "time": normalized,
            },
            "scripts": [
                {
                    "id": script["id"],
                    "name": script["name"],
                    "path": script["path"].get().strip(),
                    "enabled": bool(script["enabled"].get()),
                }
                for script in self.scripts
            ],
        }
        try:
            appconfig.save_config(cfg)
        except OSError as exc:
            if show_error:
                messagebox.showerror("保存失败", f"无法写入配置文件：\n{exc}", parent=self.root)
            self.ui_log(f"配置保存失败：{exc}")
            return False
        self.cfg = cfg
        self._recompute_next()
        return True

    def _save_config_clicked(self) -> None:
        if self._save_config(show_error=True):
            self.ui_log("配置已保存")

    def _open_log_dir(self) -> None:
        appconfig.LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(appconfig.LOG_DIR)
        except OSError as exc:
            self.ui_log(f"无法打开日志目录：{exc}")

    # ------------------------------------------------------------ 托盘 / 窗口

    def _setup_tray(self) -> None:
        if pystray is None:
            self.ui_log("提示：未安装 pystray，托盘图标不可用（pip install pystray）")
            return
        menu = pystray.Menu(
            pystray.MenuItem("打开配置", lambda *_: self.q.put(("show", None)), default=True),
            pystray.MenuItem("立即运行全部", lambda *_: self.q.put(("run", None))),
            pystray.MenuItem("停止任务", lambda *_: self.q.put(("stop", None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", lambda *_: self.q.put(("quit", None))),
        )
        self.tray = pystray.Icon("gacha_script_worker", self.icon_image, TRAY_TITLE, menu)
        try:
            self.tray.run_detached()
            self.tray_ok = True
        except Exception as exc:
            self.tray = None
            self.ui_log(f"托盘图标创建失败：{exc}")

    def _on_close(self) -> None:
        if self.tray_ok:
            self.hide_to_tray()
        else:
            self.request_quit()

    def _on_unmap(self, _event=None) -> None:
        if self.root.state() == "iconic":
            self.root.after(10, self.hide_to_tray)

    def hide_to_tray(self) -> None:
        if not self.tray_ok:
            return
        self.root.withdraw()
        if not self.hide_notified:
            self.hide_notified = True
            try:
                self.tray.notify("程序已最小化到系统托盘，双击图标可打开配置窗口。", APP_TITLE)
            except Exception:
                pass

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()

    def request_quit(self) -> None:
        if self.running:
            self.show_window()
            if not messagebox.askyesno(
                "退出程序",
                "任务正在运行，退出将停止任务并关闭相关程序。确定退出吗？",
                parent=self.root,
            ):
                return
        self._shutdown()

    def _shutdown(self) -> None:
        was_running = self.running
        self.running = False
        self.stop_event.set()
        if self.run_thread is not None and self.run_thread.is_alive():
            self.run_thread.join(timeout=10)
        if was_running:
            try:
                automation.close_target_apps(None, quiet=True)
            except Exception:
                pass
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        try:
            self._save_config(show_error=False)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------ 消息循环

    def _poll_queue(self) -> None:
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "log":
                        self.ui_log(payload)
                    elif kind == "status":
                        self.status_var.set(payload)
                    elif kind == "record":
                        self._apply_record(payload)
                    elif kind == "run_done":
                        self._on_run_done()
                    elif kind == "show":
                        self.show_window()
                    elif kind == "run":
                        self._start_run(manual=True)
                    elif kind == "stop":
                        self.stop_run()
                    elif kind == "quit":
                        self.request_quit()
                except Exception:
                    self.ui_log("界面处理消息出错：\n" + traceback.format_exc())
        finally:
            try:
                self.root.after(120, self._poll_queue)
            except tk.TclError:
                pass


def main() -> None:
    enable_dpi_awareness()
    if is_instance_running():
        show_already_running()
        return
    if handle_elevation():
        return
    lock = acquire_instance_lock()
    if lock is None:
        show_already_running()
        return

    root = tk.Tk()
    app = WorkerApp(root)
    try:
        root.mainloop()
    finally:
        try:
            if app.tray is not None:
                app.tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
