"""窗口查找、相对坐标点击、进程监控与两个脚本的执行流程。"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta

import psutil
import pyautogui
import win32api
import win32con
import win32gui
import win32process

import config as appconfig

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.15

WINDOW_TIMEOUT = 600          # 等待脚本窗口出现
GAME_START_TIMEOUT = 30 * 60  # 等待游戏进程启动
GAME_RUN_TIMEOUT = 6 * 3600   # 等待游戏进程退出
SHORT_RUN_WARN = 180          # 游戏运行时间过短的提醒阈值（秒）
CLOSE_GRACE = 5               # 游戏结束后给脚本留的处理时间
BETWEEN_SCRIPTS = 10          # 两个脚本之间的间隔
CONSOLE_RUN_TIMEOUT = 30 * 60  # 控制台脚本（米游社签到）最长运行时间
UPDATE_CHECK_WAIT = 30        # 等待三月七启动更新检查出结果
UPDATE_DIALOG_WAIT = 8        # 出现新版本后等待更新窗口渲染
ONEDRAGON_START_WAIT = 15     # 按 F9 后等待一条龙开始运行的日志标记
MAA_READY_WAIT = 90           # 等待 MAA 初始化完成（窗口出现后界面仍可能不可点击）
MAA_AUTOSTART_PROBE = 15      # 启动后多久内可能出现“自动开启模拟器”，没出现就认为未启用
MAA_AUTOSTART_WAIT = 75       # 等待 MAA 自动启动模拟器结束（MAA 内部最多等 60 秒）
MAA_START_WAIT = 20           # 点击 Link Start! 后等待 MAA 开始运行的日志标记
MAA_CLICK_RETRY_WAIT = 6      # 首次点击未生效时，等待这么久后重试
MAA_GUIDE_MAX_STEP = 7        # MAA 设置指引的步骤数；达到即视为完成
MAA_GUIDE_VERSION = 2         # MAA 6.21.2 的 CurrentGuideTutorialVersion
MAA_AUTOSTART_START_MARKERS = ("Try to start emulator", "Waiting for the emulator to start")
MAA_AUTOSTART_END_MARKERS = (
    "Waiting for the emulator is over",
    "等待结束",
    "Stop waiting for the emulator to start",
)

GAME_PROCESSES = {"starrail.exe", "zenlesszonezero.exe"}

TARGET_PROCESS_NAMES = {
    "march7th launcher.exe",
    "march7th assistant.exe",
    "march7th updater.exe",
    "onedragon-launcher.exe",
    "onedragon-runtimelauncher.exe",
    "onedragon-installer.exe",
    "maa.exe",
} | GAME_PROCESSES

# 模拟器（MuMu、雷电、BlueStacks 等）由 MAA 自己管理，不能加入上面的关闭集合

# 通过进程路径/命令行补充识别（一条龙运行时由 uv + python -m zzz_od.gui.app 组成）
TARGET_PATH_KEYS = (
    "zenlesszonezero-onedragon",
    "march7thassistant",
    "zzz_od",
    "zzzod",
    "onedragon",
)
# 命令行只对脚本运行时进程做匹配，避免误伤命令行里恰好含这些词的终端/编辑器
CMD_MATCH_EXES = {"python.exe", "pythonw.exe", "uv.exe", "uvx.exe"}
TARGET_CMD_KEYS = (
    "zenlesszonezero-onedragon",
    "march7thassistant",
    "zzz_od.gui",
)

SCRIPT_META = {
    "mihoyo": {
        "kind": "console",
        "title": "米游社签到",
        "command": "python main.py",
        "timeout": CONSOLE_RUN_TIMEOUT,
    },
    "march7th": {
        "window_title": "March7th Assistant",
        "exclude_titles": (),
        "window_processes": ("march7th launcher.exe", "march7th assistant.exe"),
        "min_window_width": 700,
        "client_size": (960, 640),
        "settle_after_window": 1.5,
        # 启动时可能弹出更新窗口，需先点“好的”跳过
        "update_prompt": True,
        # 主页“任务”区域第一张卡片：完整运行
        "click_steps": [("完整运行", 0.16354, 0.78125, 0.0)],
        "game_process": "StarRail.exe",
        "game_name": "崩坏：星穹铁道",
    },
    "zzz": {
        "window_title": "一条龙",
        "exclude_titles": ("启动器",),
        "window_processes": (
            "onedragon-launcher.exe",
            "onedragon-runtimelauncher.exe",
            "onedragon-installer.exe",
        ),
        "min_window_width": 800,
        # 一条龙自身最小高度就是 786，按 747 调整会一直是 786
        "client_size": (1140, 786),
        "settle_after_window": 2.0,
        # 左侧菜单“一条龙”；开始改用全局快捷键 F9，不再点击按钮（窗口高度变化时坐标会偏）
        "click_steps": [("菜单 一条龙", 0.05263, 0.26774, 4.0)],
        "start_hotkey": "f9",
        "game_process": "ZenlessZoneZero.exe",
        "game_name": "绝区零",
    },
    "maa": {
        "window_title": "MAA -",
        # 以管理员身份运行时，MAA 会把标题改成“MAA (管理员) - …”
        "window_titles": ("MAA -", "MAA (管理员) -"),
        "exclude_titles": ("文件资源管理器", "Updater"),
        "window_processes": ("maa.exe",),
        "min_window_width": 700,
        "client_size": (800, 600),
        "settle_after_window": 1.0,
        # MAA 窗口先出现、界面稍后才可点击，等它自己的初始化日志
        "ready_log_marker": "Init: false to true",
        "ready_timeout": MAA_READY_WAIT,
        # 新版 MAA 首次启动会显示设置指引，指引存在时没有 Link Start! 按钮
        "mark_guide_done": True,
        # 开启“启动后自动开启模拟器”时，MAA 会先进入最多 60 秒的启动等待，
        # 此期间按钮是“停止”，提前点击会中断模拟器启动
        "wait_autostart": True,
        # 左下角“Link Start!”；启动后的操作全部由 MAA 自己完成
        "click_steps": [("Link Start!", 0.18375, 0.89833, 0.0)],
        "start_log_marker": "LinkStart Enter",
        "finish_log": "debug/gui.log",
        "finish_marker": "任务已全部完成！",
        "finish_failure_markers": ("任务出错:", "连接失败", "已停止"),
        "finish_timeout": GAME_RUN_TIMEOUT,
        "game_name": "明日方舟",
    },
}


class AutomationError(RuntimeError):
    pass


class StopRequested(RuntimeError):
    pass


class RunContext:
    def __init__(self, log, set_status, on_record, stop_event):
        self._log = log
        self._set_status = set_status
        self._on_record = on_record
        self.stop_event = stop_event

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested("已停止")

    def log(self, message: str) -> None:
        self._log(message)

    def status(self, text: str) -> None:
        self._set_status(text)

    def record(self, rec: dict, final: bool = False) -> None:
        data = dict(rec)
        data["final"] = final
        self._on_record(data)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分"
    if minutes:
        return f"{minutes}分{secs:02d}秒"
    return f"{secs}秒"


# ---------------------------------------------------------------- 进程

def _proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return ""


def is_process_running(name: str) -> bool:
    target = name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _target_window_pids() -> set[int]:
    """按窗口标题兜底识别一条龙（排除资源管理器的同名文件夹窗口）。"""
    pids: set[int] = set()

    def enum_cb(hwnd, _param):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if not win32gui.GetClassName(hwnd).startswith("Qt"):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            if "一条龙" not in title and "onedragon" not in title.lower():
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid:
                pids.add(pid)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(enum_cb, None)
    return pids


def _protected_pids() -> set[int]:
    """总控自身及其启动链（终端/资源管理器等）绝不能被当作目标关闭。"""
    protected = {os.getpid()}
    try:
        protected.update(parent.pid for parent in psutil.Process().parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return protected


def iter_targets() -> list[psutil.Process]:
    """三月七 / 一条龙 / MAA / 星铁 / 绝区零 相关的全部进程（不含模拟器）。"""
    protected = _protected_pids()
    window_pids = _target_window_pids()
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        if proc.pid in protected:
            continue
        try:
            name = (proc.info["name"] or "").lower()
            exe = (proc.info["exe"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cmd_hit = name in CMD_MATCH_EXES and any(key in cmd for key in TARGET_CMD_KEYS)
        hit = (
            name in TARGET_PROCESS_NAMES
            or "onedragon" in name
            or "march7th" in name
            or proc.pid in window_pids
            or any(key in exe for key in TARGET_PATH_KEYS)
            or cmd_hit
        )
        if hit:
            found.append(proc)
    return found


def _wait_terminated(procs: list[psutil.Process], timeout: float) -> list[psutil.Process]:
    """等待进程退出；部分进程权限受限时 psutil.wait_procs 会抛异常，这里兜底。"""
    try:
        _, alive = psutil.wait_procs(procs, timeout=timeout)
        return alive
    except (psutil.AccessDenied, OSError):
        alive = []
        for proc in procs:
            try:
                if proc.is_running():
                    alive.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return alive


def _request_maa_close(procs: list[psutil.Process]) -> None:
    """先让 MAA 走正常关闭流程，避免强制结束打断配置写入。"""
    pids = set()
    for proc in procs:
        try:
            if (proc.name() or "").lower() == "maa.exe":
                pids.add(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not pids:
        return

    def enum_cb(hwnd, _extra) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32process.GetWindowThreadProcessId(hwnd)[1] not in pids:
                return
            if win32gui.GetWindowText(hwnd).startswith("MAA"):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)


def close_target_apps(ctx: RunContext | None = None, quiet: bool = False) -> bool:
    def log(text: str) -> None:
        if ctx is not None:
            ctx.log(text)

    procs = iter_targets()
    if not procs:
        if not quiet:
            log("相关程序均未运行")
        return True

    names = []
    for proc in procs:
        try:
            names.append(proc.name())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    log("正在关闭：" + "、".join(sorted(set(names))))

    candidates: dict[int, psutil.Process] = {}
    for proc in procs:
        try:
            for child in proc.children(recursive=True):
                candidates[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        candidates[proc.pid] = proc

    targets = list(candidates.values())
    maa_targets = []
    for proc in targets:
        try:
            if (proc.name() or "").lower() == "maa.exe":
                maa_targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if maa_targets:
        _request_maa_close(maa_targets)
        _wait_terminated(maa_targets, timeout=3)

    denied = False
    for proc in targets:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied = True
    alive = _wait_terminated(targets, timeout=6)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied = True
    _wait_terminated(alive, timeout=5)

    deadline = time.time() + 10
    while time.time() < deadline:
        remaining = iter_targets()
        if not remaining:
            log("相关程序已全部关闭")
            return True
        for proc in remaining:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied = True
        time.sleep(0.5)

    remaining = iter_targets()
    if remaining:
        try:
            names = sorted({p.name() for p in remaining})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            names = ["未知进程"]
        message = "警告：以下进程未能关闭：" + "、".join(names)
        if denied and not is_elevated():
            message += "（权限不足，请以管理员身份运行总控程序）"
        log(message)
        return False
    log("相关程序已全部关闭")
    return True


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def launch_script(path: str) -> None:
    path = appconfig.resolve_path(path)
    try:
        subprocess.Popen([path], cwd=os.path.dirname(path))
    except OSError as exc:
        if getattr(exc, "winerror", None) == 740:
            raise AutomationError(
                "该程序要求管理员权限，无法以普通权限启动。请以管理员身份运行总控程序后重试。"
            ) from exc
        raise AutomationError(f"无法启动脚本：{exc}") from exc


# ---------------------------------------------------------------- 控制台脚本

def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return text.replace("'", "''")


def _close_console(proc: subprocess.Popen) -> bool:
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=3)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False


def open_script_manually(script_id: str, path: str) -> None:
    """手动打开脚本本体：GUI 脚本启动程序，控制台脚本在目录中执行命令。"""
    meta = SCRIPT_META[script_id]
    path = appconfig.resolve_path(path)
    if meta.get("kind") == "console":
        if not os.path.isdir(path):
            raise AutomationError(f"脚本目录不存在：{path}")
        title = f"{meta['title']}（手动）"
        command = (
            f"$Host.UI.RawUI.WindowTitle = '{_ps_quote(title)}'; "
            f"Set-Location -LiteralPath '{_ps_quote(path)}'; "
            f"{meta['command']}"
        )
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NoLogo", "-NoExit", "-Command", command],
                cwd=path,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except OSError as exc:
            raise AutomationError(f"无法启动 PowerShell：{exc}") from exc
        return
    if not os.path.isfile(path):
        raise AutomationError(f"脚本路径不存在：{path}")
    launch_script(path)


def _run_console(ctx: RunContext, script: dict, meta: dict, rec: dict, folder: str) -> None:
    """在脚本目录打开 PowerShell 执行命令，命令结束回到提示符即视为完成。

    成功后关闭该窗口；失败或超时时保持窗口可见，便于查看原因。
    """
    folder = appconfig.resolve_path(folder)
    if not os.path.isdir(folder):
        raise AutomationError(f"脚本目录不存在：{folder}")

    token = uuid.uuid4().hex[:6]
    window_title = f"{meta['title']} {token}"
    marker = os.path.join(tempfile.gettempdir(), f"worker_console_{token}.done")
    try:
        if os.path.exists(marker):
            os.remove(marker)
    except OSError:
        pass

    command = (
        f"$Host.UI.RawUI.WindowTitle = '{_ps_quote(window_title)}'; "
        f"Set-Location -LiteralPath '{_ps_quote(folder)}'; "
        "$worker_code = -999; "
        f"try {{ {meta['command']}; "
        "if ($null -ne $LASTEXITCODE) { $worker_code = $LASTEXITCODE } } "
        "catch { $worker_code = -998 }; "
        f"[System.IO.File]::WriteAllText('{_ps_quote(marker)}', [string]$worker_code)"
    )

    ctx.log(f"在目录中执行：{meta['command']}（{folder}）")
    try:
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NoLogo", "-NoExit", "-Command", command],
            cwd=folder,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except OSError as exc:
        raise AutomationError(f"无法启动 PowerShell：{exc}") from exc

    rec["launched"] = True
    rec["state"] = "等待执行完成"
    ctx.record(rec)
    ctx.status(f"{script['name']}：等待执行完成（请勿关闭该窗口）")
    ctx.log("等待脚本执行完成（命令退出并回到 PowerShell 提示符时判定完成）…")

    started = time.time()
    last_note = 0.0
    while not os.path.exists(marker):
        ctx.check_stop()
        if proc.poll() is not None:
            raise AutomationError("PowerShell 窗口已被关闭，脚本未执行完成")
        if time.time() - started > meta["timeout"]:
            raise AutomationError(
                f"等待脚本执行超时（{int(meta['timeout'] // 60)} 分钟），PowerShell 窗口保持打开"
            )
        if time.time() - last_note > 60:
            last_note = time.time()
            ctx.log(f"脚本仍在执行…（已 {fmt_duration(time.time() - started)}）")
        time.sleep(1.0)

    elapsed = time.time() - started
    code = None
    try:
        with open(marker, encoding="utf-8", errors="ignore") as handle:
            code = int(handle.read().strip())
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.remove(marker)
        except OSError:
            pass

    if code != 0:
        if code is None:
            note = "已回到提示符，但无法读取退出代码，请查看 PowerShell 窗口"
        elif code in (-998, -999):
            note = "无法正常执行命令（可能是未找到 python），请查看 PowerShell 窗口"
        else:
            note = f"命令退出代码 {code}，请查看 PowerShell 窗口"
        rec["completed"] = False
        rec["state"] = "失败"
        rec["note"] = note
        ctx.record(rec)
        ctx.log(f"{script['name']} 执行异常：{note}")
        return

    time.sleep(0.5)
    closed = _close_console(proc)
    if closed:
        ctx.log("PowerShell 窗口已关闭")
    else:
        ctx.log("提示：PowerShell 窗口未能自动关闭，请手动关闭")

    rec["completed"] = True
    rec["state"] = "已完成"
    note = f"命令执行完毕（用时 {fmt_duration(elapsed)}）"
    note += "，PowerShell 窗口已关闭" if closed else "，PowerShell 窗口未能自动关闭"
    rec["note"] = note
    ctx.record(rec)
    ctx.log(f"{script['name']} 运行完成，用时 {fmt_duration(elapsed)}")


# ---------------------------------------------------------------- 窗口

def _window_proc_name(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return ""
    return _proc_name(pid)


def find_script_window(meta: dict) -> int | None:
    matches: list[tuple[int, int]] = []

    title_keys = meta.get("window_titles") or (meta["window_title"],)

    def enum_cb(hwnd, _param):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title or not any(key in title for key in title_keys):
                return True
            if any(word in title for word in meta.get("exclude_titles", ())):
                return True
            minimized = win32gui.IsIconic(hwnd)
            if not minimized:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left < meta.get("min_window_width", 600):
                    return True
            proc_name = _window_proc_name(hwnd)
            score = 2 if proc_name in meta.get("window_processes", ()) else 0
            matches.append((score, hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], -item[1]))
    return matches[0][1]


def wait_for_window(ctx: RunContext, meta: dict, timeout: float) -> int:
    started = time.time()
    last_note = 0.0
    while time.time() - started < timeout:
        ctx.check_stop()
        hwnd = find_script_window(meta)
        if hwnd:
            return hwnd
        if time.time() - last_note > 30:
            last_note = time.time()
            title_keys = meta.get("window_titles") or (meta["window_title"],)
            ctx.log("仍在等待脚本窗口「" + " / ".join(title_keys) + "」…")
        time.sleep(1.5)
    raise AutomationError(f"等待脚本窗口超时（{int(timeout // 60)} 分钟）")


def get_client_size(hwnd: int) -> tuple[int, int]:
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return right - left, bottom - top


def resize_client(hwnd: int, width: int, height: int) -> tuple[int, int]:
    """把窗口客户区调整为固定尺寸，保证相对坐标稳定。"""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.6)
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    cw, ch = get_client_size(hwnd)
    extra_w, extra_h = (wr - wl) - cw, (wb - wt) - ch
    target_w, target_h = width + extra_w, height + extra_h

    screen_w = win32api.GetSystemMetrics(0)
    screen_h = win32api.GetSystemMetrics(1)
    x = max(0, min(wl, screen_w - target_w))
    y = max(0, min(wt, screen_h - target_h))

    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOP,
        x,
        y,
        target_w,
        target_h,
        win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.5)
    return get_client_size(hwnd)


def ensure_foreground(hwnd: int, ctx: RunContext | None = None) -> bool:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.2)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    foreground = win32gui.GetForegroundWindow()
    fg_thread = 0
    if foreground:
        try:
            fg_thread = win32process.GetWindowThreadProcessId(foreground)[0]
        except Exception:
            fg_thread = 0
    win_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
    attached = False
    try:
        if fg_thread and fg_thread != win_thread:
            attached = bool(ctypes.windll.user32.AttachThreadInput(fg_thread, win_thread, True))
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(fg_thread, win_thread, False)

    time.sleep(0.3)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    # 系统前台锁定时，用一次真实点击标题栏取得焦点
    try:
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        cw, ch = get_client_size(hwnd)
        pyautogui.click(left + cw // 2, top + max(10, int(ch * 0.03)))
        time.sleep(0.4)
    except Exception:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    if ctx is not None:
        ctx.log("警告：无法把脚本窗口切换到前台，本次点击可能无效")
    return False


def click_client(hwnd: int, fx: float, fy: float) -> None:
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    cw, ch = get_client_size(hwnd)
    x = left + int(round(cw * fx))
    y = top + int(round(ch * fy))
    pyautogui.moveTo(x, y, duration=0.2)
    time.sleep(0.15)
    pyautogui.click()


# ---------------------------------------------------------------- 三月七更新提示

UPDATE_FOUND_RE = re.compile(r"GitHub 发现新版本[:：]\s*(v[\d][\w.\-]*)")
UPDATE_DIALOG_TITLE_RE = re.compile(r"发现新版本[:：].*?[—\-]+[>→]\s*(v[\d][\w.\-]*)")
UPDATE_NONE_MARKERS = ("已经是最新版本", "确认已是最新版本", "检测更新失败")


def _parse_update_version(text: str) -> str:
    match = UPDATE_FOUND_RE.search(text) or UPDATE_DIALOG_TITLE_RE.search(text)
    return match.group(1) if match else ""


def _capture_window(hwnd: int):
    """截取窗口区域，返回 (图片, 窗口左上角屏幕坐标)。"""
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left < 100 or bottom - top < 100:
            return None
        img = pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        return img, left, top
    except Exception:
        return None


def _find_update_ok_button(img) -> tuple[int, int] | None:
    """在三月七更新窗口里定位“好的”按钮。

    按钮本身是白色，“手动下载”是其右侧等宽的粉色按钮。
    以粉色横条为锚点，取其左侧等宽位置即可，无需 OCR。
    """
    width, height = img.size
    pixels = img.load()
    bars: list[tuple[int, int, int]] = []
    for y in range(int(height * 0.40), int(height * 0.97), 2):
        start = None
        last = None
        for x in range(int(width * 0.05), int(width * 0.98), 2):
            r, g, b = pixels[x, y][:3]
            pink = r > 200 and 90 < g < 190 and 140 < b < 230 and r - g > 40 and b - g > 10
            if pink:
                if start is None:
                    start = x
                last = x
            elif start is not None:
                if last - start >= 180:
                    bars.append((y, start, last))
                start = None
        if start is not None and last - start >= 180:
            bars.append((y, start, last))
    if not bars:
        return None
    y = (min(bar[0] for bar in bars) + max(bar[0] for bar in bars)) // 2
    nearest = min(bars, key=lambda bar: abs(bar[0] - y))
    left, right = nearest[1], nearest[2]
    # “好的”与“手动下载”等宽，中间约 12px 间隔
    return left - (right - left) // 2 - 12, y


def _dismiss_update_dialog(ctx: RunContext, hwnd: int, origin: tuple[int, int],
                           point: tuple[int, int]) -> bool:
    """点击“好的”关闭更新窗口；返回是否确认关闭。"""
    for attempt in range(2):
        ctx.check_stop()
        ensure_foreground(hwnd, ctx)
        left, top = origin
        pyautogui.click(left + point[0], top + point[1])
        ctx.log(f"已点击「好的」跳过更新（第 {attempt + 1} 次）")
        time.sleep(1.2)
        shot = _capture_window(hwnd)
        if not shot:
            return True
        current = _find_update_ok_button(shot[0])
        if not current:
            return True
        point = current
        origin = (shot[1], shot[2])
    return False


def _wait_update_prompt(ctx: RunContext, hwnd: int, log_path: str, offset: int) -> str:
    """处理三月七启动时的更新提示，返回写入执行说明的文字。

    是否有新版本从三月七自己的日志读取；更新窗口是否存在用固定位置的
    粉色“手动下载”按钮判断（不依赖 OCR）。
    """
    version = ""
    dialog = None
    deadline = time.time() + UPDATE_CHECK_WAIT
    while time.time() < deadline:
        ctx.check_stop()
        shot = _capture_window(hwnd)
        if shot:
            point = _find_update_ok_button(shot[0])
            if point:
                dialog = (point, (shot[1], shot[2]))
                break
        text = _read_log_since(log_path, offset) if log_path else None
        if text:
            version = _parse_update_version(text)
            if version:
                break
            if any(marker in text for marker in UPDATE_NONE_MARKERS):
                return ""
        time.sleep(0.5)

    if not dialog and version:
        deadline = time.time() + UPDATE_DIALOG_WAIT
        while time.time() < deadline:
            ctx.check_stop()
            shot = _capture_window(hwnd)
            if shot:
                point = _find_update_ok_button(shot[0])
                if point:
                    dialog = (point, (shot[1], shot[2]))
                    break
            time.sleep(0.5)

    if dialog:
        if not version and log_path:
            version = _parse_update_version(_read_log_since(log_path, offset) or "")
        ctx.log(f"检测到三月七新版本 {version}，点击「好的」跳过更新…" if version
                else "检测到三月七更新提示窗口，点击「好的」跳过…")
        if not _dismiss_update_dialog(ctx, hwnd, dialog[1], dialog[0]):
            raise AutomationError("三月七更新提示窗口未能关闭，请手动点击「好的」后重试")
        return f"三月七有可用更新（{version}），已点击「好的」跳过更新" if version \
            else "三月七有可用更新，已点击「好的」跳过更新"

    if version:
        ctx.log("未出现更新提示窗口，继续运行（说明中会标注需要更新）")
        return f"三月七有可用更新（{version}），未弹出更新窗口"
    return ""


# ---------------------------------------------------------------- 一条龙快捷键启动

ONEDRAGON_START_MARKERS = ("已启用前台模式", "已启用后台模式")


def _read_log_since(path: str, offset: int) -> str | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return None


def _press_hotkey_and_check(ctx: RunContext, hwnd: int, log_path: str, key: str,
                            wait_seconds: float) -> bool | None:
    """按快捷键启动一条龙；用一条龙自己的日志确认是否真的开始运行。

    返回 True=已开始，False=未开始，None=日志不可读无法确认。
    """
    try:
        offset = os.path.getsize(log_path)
    except OSError:
        offset = None

    ensure_foreground(hwnd, ctx)
    pyautogui.press(key)
    ctx.log(f"已按下 {key.upper()} 开始运行")

    if offset is None:
        return None
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        ctx.check_stop()
        text = _read_log_since(log_path, offset)
        if text is None:
            return None
        if any(marker in text for marker in ONEDRAGON_START_MARKERS):
            return True
        time.sleep(0.4)
    return False


def _start_onedragon(ctx: RunContext, hwnd: int, meta: dict, app_dir: str) -> None:
    """用全局快捷键 F9 开始一条龙，避免点击“开始 F9”按钮时因窗口高度变化而点偏。"""
    key = meta["start_hotkey"]
    log_path = os.path.join(app_dir, ".log", "log.txt")
    ctx.log("使用一条龙的全局快捷键开始运行（不依赖按钮坐标）…")
    started = _press_hotkey_and_check(ctx, hwnd, log_path, key, ONEDRAGON_START_WAIT)
    if started is False:
        label, fx, fy, _wait = meta["click_steps"][0]
        ctx.log(f"未检测到开始运行，重新点击「{label}」后重试…")
        ensure_foreground(hwnd, ctx)
        click_client(hwnd, fx, fy)
        time.sleep(2.0)
        started = _press_hotkey_and_check(ctx, hwnd, log_path, key, ONEDRAGON_START_WAIT)
    if started is False:
        raise AutomationError("按 F9 后一条龙仍未开始运行，请检查一条龙窗口状态")
    if started is None:
        ctx.log("提示：无法读取一条龙日志，已按 F9，继续等待游戏启动")
    else:
        ctx.log("一条龙已开始运行")


# ---------------------------------------------------------------- MAA 日志完成判定

def _read_log_after(path: str, offset: int) -> tuple[str | None, int]:
    """只读取 offset 之后的新日志；文件被截断时从头读，避免扫描历史大日志。"""
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
            return data.decode("utf-8", errors="ignore"), handle.tell()
    except OSError:
        return None, offset


def _find_log_marker(text: str, marker: str, since: datetime) -> bool:
    """匹配“开始时间之后”的带时间戳日志行，防止误用上一轮残留标记。"""
    pattern = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]"
        r"\[[A-Z]+\]\[[^\]]+\][^\n]*?" + re.escape(marker)
    )
    for match in pattern.finditer(text):
        try:
            line_time = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if line_time >= since - timedelta(seconds=3):
            return True
    return False


def _mark_maa_guide_done(app_dir: str, ctx: RunContext) -> None:
    """把 MAA 的设置指引标记为已完成，否则新版首次启动只有指引、没有 Link Start! 按钮。"""
    path = os.path.join(app_dir, "config", "gui.new.json")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        ctx.log("提示：无法读取 MAA 配置，跳过设置指引检查")
        return

    gui = data.get("Gui")
    if not isinstance(gui, dict):
        return
    step = gui.get("GuideStep", 0)
    version = gui.get("GuideTutorialVersion", 0)
    if isinstance(step, int) and isinstance(version, int) \
            and step >= MAA_GUIDE_MAX_STEP and version >= MAA_GUIDE_VERSION:
        return

    gui["GuideStep"] = MAA_GUIDE_MAX_STEP
    gui["GuideTutorialVersion"] = MAA_GUIDE_VERSION
    temp = path + ".worker.tmp"
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
        os.replace(temp, path)
        ctx.log("已将 MAA 设置指引标记为完成，避免指引界面挡住 Link Start!")
    except OSError as exc:
        try:
            os.remove(temp)
        except OSError:
            pass
        ctx.log(f"提示：写入 MAA 设置指引状态失败：{exc}")


def _dismiss_maa_dialogs(ctx: RunContext, hwnd: int) -> bool:
    """关闭 MAA 的更新提示窗口；MAA 的“稍后”按钮等同于 WM_CLOSE。"""
    try:
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return False

    dialogs: list[tuple[int, str]] = []

    def enum_cb(dialog: int, _extra) -> None:
        try:
            if dialog == hwnd or not win32gui.IsWindowVisible(dialog):
                return
            if win32process.GetWindowThreadProcessId(dialog)[1] != pid:
                return
            title = win32gui.GetWindowText(dialog)
            if title and any(key in title for key in ("新版本", "立即更新", "更新 MAA")):
                dialogs.append((dialog, title))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    for dialog, title in dialogs:
        ctx.check_stop()
        ctx.log(f"检测到 MAA 更新提示「{title}」，点击「稍后」跳过…")
        try:
            win32gui.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
        except Exception:
            continue
        time.sleep(0.4)
        if win32gui.IsWindow(dialog) and win32gui.IsWindowVisible(dialog):
            ctx.log("MAA 更新提示尚未关闭，稍后重试")
    return bool(dialogs)


def _wait_maa_ready(ctx: RunContext, hwnd: int, log_path: str, marker: str,
                    since: datetime, offset: int, timeout: float) -> int:
    """等 MAA 初始化完成；窗口出现不代表界面已经能接受点击。"""
    deadline = time.time() + timeout
    last_note = 0.0
    while time.time() < deadline:
        ctx.check_stop()
        text, offset = _read_log_after(log_path, offset)
        if text and _find_log_marker(text, marker, since):
            ctx.log("MAA 初始化完成，界面已就绪")
            return offset
        if not win32gui.IsWindow(hwnd):
            raise AutomationError("MAA 窗口已关闭，界面未就绪")
        if time.time() - last_note > 20:
            last_note = time.time()
            ctx.log("等待 MAA 初始化…")
        time.sleep(0.4)
    ctx.log(f"提示：{int(timeout)} 秒内未看到 MAA 初始化标记，继续尝试启动")
    return offset


def _wait_maa_autostart(ctx: RunContext, hwnd: int, log_path: str,
                        since: datetime, offset: int) -> int:
    """等 MAA 自己“启动后自动开启模拟器”的阶段结束再点 Link Start!。

    这个阶段内左下角按钮是“停止”，提前点击会中断模拟器启动并让任务无法开始。
    """
    entered = False
    wait_deadline = 0.0
    probe_deadline = time.time() + MAA_AUTOSTART_PROBE
    last_note = 0.0
    while True:
        ctx.check_stop()
        _dismiss_maa_dialogs(ctx, hwnd)
        text, offset = _read_log_after(log_path, offset)
        if text:
            if not entered and any(
                    _find_log_marker(text, item, since)
                    for item in MAA_AUTOSTART_START_MARKERS):
                entered = True
                wait_deadline = time.time() + MAA_AUTOSTART_WAIT
                ctx.log("MAA 正在自动启动模拟器，等待启动完成后再点 Link Start!…")
            if entered and any(
                    _find_log_marker(text, item, since)
                    for item in MAA_AUTOSTART_END_MARKERS):
                ctx.log("MAA 自动启动模拟器阶段已结束")
                return offset
        if not win32gui.IsWindow(hwnd):
            raise AutomationError("MAA 窗口已关闭，模拟器未就绪")

        now = time.time()
        if entered:
            if now > wait_deadline:
                raise AutomationError(
                    f"等待 MAA 自动启动模拟器超时（{int(MAA_AUTOSTART_WAIT)} 秒）"
                )
            if now - last_note > 15:
                last_note = now
                ctx.log("仍在等待 MAA 自动启动模拟器…")
        elif now > probe_deadline:
            ctx.log("MAA 未进入自动启动模拟器流程，继续点击 Link Start!")
            return offset
        time.sleep(0.5)


def _start_maa(ctx: RunContext, hwnd: int, log_path: str, marker: str,
               since: datetime, offset: int, label: str, fx: float, fy: float) -> int:
    """点击 Link Start! 并用 MAA 日志确认已开始；首次点空时自动重试。"""
    for attempt in (1, 2):
        _dismiss_maa_dialogs(ctx, hwnd)
        ensure_foreground(hwnd, ctx)
        click_client(hwnd, fx, fy)
        ctx.log(f"已点击：{label}" + ("" if attempt == 1 else "（重试）"))
        wait_seconds = MAA_CLICK_RETRY_WAIT if attempt == 1 else MAA_START_WAIT
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            ctx.check_stop()
            _dismiss_maa_dialogs(ctx, hwnd)
            text, offset = _read_log_after(log_path, offset)
            if text:
                if _find_log_marker(text, marker, since):
                    return offset
                # 已经开始运行但还没写出启动标记时，绝不能再点一次，否则会按到“停止”
                if _find_log_marker(text, "BeginRun: owner=", since) \
                        or _find_log_marker(text, "Idle: true to false (called from BeginRun)", since):
                    ctx.log("MAA 已开始运行（日志已出现 BeginRun）")
                    return offset
            if not win32gui.IsWindow(hwnd):
                raise AutomationError("MAA 窗口已关闭，任务未开始")
            time.sleep(0.5)
        if attempt == 1:
            ctx.log("未检测到 MAA 开始标记，重试点击 Link Start!…")
            time.sleep(1.0)
    raise AutomationError("点击 Link Start! 后 MAA 未开始运行，请检查 MAA 窗口和 debug/gui.log")


def _wait_maa_completion(ctx: RunContext, hwnd: int, log_path: str, marker: str,
                         since: datetime, offset: int, timeout: float,
                         started_at: float, failure_markers: tuple[str, ...] = ()) -> float:
    """等 MAA 日志出现当前这轮的“任务已全部完成！”，不依赖模拟器退出。"""
    deadline = time.time() + timeout
    last_note = 0.0
    while time.time() < deadline:
        ctx.check_stop()
        _dismiss_maa_dialogs(ctx, hwnd)
        text, offset = _read_log_after(log_path, offset)
        if text and _find_log_marker(text, marker, since):
            ctx.log(f"检测到 MAA 日志：{marker}")
            return time.time() - started_at
        if text:
            for failure in failure_markers:
                if _find_log_marker(text, failure, since):
                    raise AutomationError(f"MAA 日志显示任务未完成（{failure}）")
        if not win32gui.IsWindow(hwnd):
            raise AutomationError("MAA 窗口已关闭，任务未完成")
        if time.time() - last_note > 60:
            last_note = time.time()
            ctx.log(f"等待 MAA 任务完成…（已 {fmt_duration(time.time() - started_at)}）")
        time.sleep(1.0)
    raise AutomationError(f"等待 MAA 任务完成超时（{fmt_duration(timeout)}）")


# ---------------------------------------------------------------- 游戏等待

def wait_game_start(ctx: RunContext, meta: dict, hwnd: int, timeout: float) -> None:
    started = time.time()
    last_note = 0.0
    missing_window_since = None
    while time.time() - started < timeout:
        ctx.check_stop()
        if is_process_running(meta["game_process"]):
            return
        if not win32gui.IsWindow(hwnd):
            if missing_window_since is None:
                missing_window_since = time.time()
            elif time.time() - missing_window_since > 8:
                raise AutomationError("脚本窗口已关闭，游戏未启动")
        else:
            missing_window_since = None
        if time.time() - last_note > 60:
            last_note = time.time()
            ctx.log(
                f"等待游戏 {meta['game_name']} 启动…（已 {fmt_duration(time.time() - started)}）"
            )
        time.sleep(2)
    raise AutomationError(f"等待游戏启动超时（{int(timeout // 60)} 分钟），请检查脚本是否正常开始")


def wait_game_exit(ctx: RunContext, meta: dict, timeout: float) -> float:
    game = meta["game_process"]
    started = time.time()
    while time.time() - started < timeout:
        ctx.check_stop()
        if not is_process_running(game):
            time.sleep(3)
            if not is_process_running(game):
                return time.time() - started
        time.sleep(2)
    raise AutomationError(f"等待游戏退出超时（{int(timeout // 3600)} 小时）")


# ---------------------------------------------------------------- 流程

def _new_record(script: dict) -> dict:
    return {
        "rid": uuid.uuid4().hex[:12],
        "id": script["id"],
        "name": script["name"],
        "start": now_str(),
        "end": "",
        "duration": 0.0,
        "launched": None,
        "completed": None,
        "state": "准备中",
        "note": "",
        "_t0": time.time(),
    }


def _save_failure_shot(hwnd: int, script_id: str, ctx: RunContext) -> None:
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        shot = pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = appconfig.LOG_DIR / f"failure_{script_id}_{stamp}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        shot.save(path)
        ctx.log(f"已保存窗口截图：{path}")
    except Exception:
        pass


def _mark_failure(rec: dict, ctx: RunContext, note: str) -> None:
    rec["completed"] = False
    if rec["launched"] is None:
        rec["launched"] = False
    rec["state"] = "失败"
    rec["note"] = note
    ctx.record(rec)
    ctx.log(f"{rec['name']} 执行失败：{note}")


def _run_one(ctx: RunContext, script: dict, meta: dict, rec: dict) -> None:
    hwnd = None
    update_note = ""
    try:
        path = appconfig.resolve_path(script["path"])
        if meta.get("kind") == "console":
            rec["state"] = "启动脚本"
            ctx.record(rec)
            _run_console(ctx, script, meta, rec, path)
            return

        if not os.path.isfile(path):
            raise AutomationError(f"脚本路径不存在：{path}")

        update_log = ""
        update_log_offset = 0
        if meta.get("update_prompt"):
            # 三月七会把更新检查结果写进当天日志，启动前记下位置以便只看新内容
            update_log = os.path.join(
                os.path.dirname(path), "logs", datetime.now().strftime("%Y-%m-%d.log")
            )
            try:
                update_log_offset = os.path.getsize(update_log)
            except OSError:
                update_log_offset = 0

        finish_log_path = ""
        finish_log_offset = 0
        finish_since = None
        finish_started_at = 0.0
        if meta.get("finish_log"):
            # 启动前记下日志末尾，后续只看这一轮新增的内容
            finish_log_path = os.path.join(os.path.dirname(path), meta["finish_log"])
            finish_since = datetime.now()
            finish_started_at = time.time()
            try:
                finish_log_offset = os.path.getsize(finish_log_path)
            except OSError:
                finish_log_offset = 0

        if meta.get("mark_guide_done"):
            _mark_maa_guide_done(os.path.dirname(path), ctx)

        rec["state"] = "启动脚本"
        ctx.record(rec)
        ctx.log(f"启动脚本：{path}")
        launch_script(path)

        ctx.status(f"{script['name']}：等待脚本窗口")
        ctx.log("等待脚本窗口出现…")
        hwnd = wait_for_window(ctx, meta, WINDOW_TIMEOUT)
        ctx.log(f"已找到窗口：{win32gui.GetWindowText(hwnd)!r}")

        cw, ch = resize_client(hwnd, *meta["client_size"])
        expect = meta["client_size"]
        if abs(cw - expect[0]) > 6 or abs(ch - expect[1]) > 6:
            ctx.log(f"提示：窗口客户区为 {cw}x{ch}，与预期 {expect[0]}x{expect[1]} 有差异")
        else:
            ctx.log(f"窗口客户区已调整为 {cw}x{ch}")

        ensure_foreground(hwnd, ctx)
        time.sleep(meta.get("settle_after_window", 1.5))

        if meta.get("ready_log_marker"):
            ctx.status(f"{script['name']}：等待界面就绪")
            finish_log_offset = _wait_maa_ready(
                ctx,
                hwnd,
                finish_log_path,
                meta["ready_log_marker"],
                finish_since,
                finish_log_offset,
                float(meta.get("ready_timeout", MAA_READY_WAIT)),
            )
            ensure_foreground(hwnd, ctx)
            time.sleep(0.3)

        if meta.get("wait_autostart"):
            ctx.status(f"{script['name']}：等待模拟器就绪")
            finish_log_offset = _wait_maa_autostart(
                ctx, hwnd, finish_log_path, finish_since, finish_log_offset
            )
            ensure_foreground(hwnd, ctx)
            time.sleep(0.3)

        if meta.get("update_prompt"):
            ctx.status(f"{script['name']}：等待更新检查")
            update_note = _wait_update_prompt(ctx, hwnd, update_log, update_log_offset)
            if update_note:
                rec["note"] = update_note
                ctx.record(rec)
            ensure_foreground(hwnd, ctx)
            time.sleep(0.5)

        ctx.status(f"{script['name']}：点击开始")
        if meta.get("start_log_marker"):
            label, fx, fy, _wait = meta["click_steps"][0]
            finish_log_offset = _start_maa(
                ctx,
                hwnd,
                finish_log_path,
                meta["start_log_marker"],
                finish_since,
                finish_log_offset,
                label,
                fx,
                fy,
            )
            finish_started_at = time.time()
        else:
            for label, fx, fy, wait_after in meta["click_steps"]:
                ctx.check_stop()
                ensure_foreground(hwnd, ctx)
                click_client(hwnd, fx, fy)
                ctx.log(f"已点击：{label}")
                if wait_after:
                    time.sleep(wait_after)

        if meta.get("start_hotkey"):
            ctx.status(f"{script['name']}：开始运行")
            _start_onedragon(ctx, hwnd, meta, os.path.dirname(path))

        if meta.get("finish_log"):
            ctx.log("MAA 已开始运行，等待任务全部完成…")
            rec["launched"] = True
            rec["state"] = "MAA 运行中"
            run_note = "MAA 已开始运行"
            rec["note"] = f"{update_note}；{run_note}" if update_note else run_note
            ctx.record(rec)
            ctx.status(f"{script['name']}：MAA 运行中")

            maa_seconds = _wait_maa_completion(
                ctx,
                hwnd,
                finish_log_path,
                meta["finish_marker"],
                finish_since,
                finish_log_offset,
                float(meta.get("finish_timeout", GAME_RUN_TIMEOUT)),
                finish_started_at,
                meta.get("finish_failure_markers", ()),
            )
            rec["completed"] = True
            rec["state"] = "已完成"
            run_note = f"MAA 报告任务已全部完成（用时 {fmt_duration(maa_seconds)}）"
            rec["note"] = f"{update_note}；{run_note}" if update_note else run_note
            ctx.record(rec)
            ctx.log(f"{script['name']} 运行完成，MAA 任务已全部完成，用时 {fmt_duration(maa_seconds)}")
            return

        rec["launched"] = True
        rec["state"] = "等待游戏启动"
        ctx.record(rec)
        ctx.status(f"{script['name']}：等待游戏启动")
        ctx.log(f"等待游戏进程 {meta['game_process']} 启动…")
        wait_game_start(ctx, meta, hwnd, GAME_START_TIMEOUT)

        rec["state"] = "游戏运行中"
        run_note = f"游戏进程 {meta['game_process']} 已启动"
        rec["note"] = f"{update_note}；{run_note}" if update_note else run_note
        ctx.record(rec)
        ctx.status(f"{script['name']}：游戏运行中")
        ctx.log(f"游戏 {meta['game_name']} 已启动，等待游戏关闭…")

        game_seconds = wait_game_exit(ctx, meta, GAME_RUN_TIMEOUT)

        rec["completed"] = True
        rec["state"] = "已完成"
        run_note = f"游戏运行 {fmt_duration(game_seconds)} 后关闭"
        if game_seconds < SHORT_RUN_WARN:
            run_note += "（运行时间过短，请确认是否正常完成）"
        rec["note"] = f"{update_note}；{run_note}" if update_note else run_note
        ctx.record(rec)
        ctx.log(f"{script['name']} 运行完成，游戏运行 {fmt_duration(game_seconds)}")
    except AutomationError as exc:
        _mark_failure(rec, ctx, f"{update_note}；{exc}" if update_note else str(exc))
        if hwnd:
            _save_failure_shot(hwnd, script["id"], ctx)
        raise
    except StopRequested:
        rec["completed"] = False
        rec["state"] = "已停止"
        rec["note"] = "收到停止指令"
        if meta.get("kind") == "console":
            rec["note"] = "已停止等待；PowerShell 窗口保持打开"
        ctx.record(rec)
        raise
    except Exception as exc:
        note = f"操作失败：{exc}"
        text = str(exc)
        if "拒绝访问" in text or "Access is denied" in text or getattr(exc, "winerror", None) == 5:
            note += "（权限不足，请以管理员身份运行总控程序）"
        _mark_failure(rec, ctx, f"{update_note}；{note}" if update_note else note)
        if hwnd:
            _save_failure_shot(hwnd, script["id"], ctx)
        raise AutomationError(note) from exc


def run_sequence(ctx: RunContext, scripts: list[dict]) -> None:
    started_at = time.time()
    ctx.log("开始执行任务序列：" + " → ".join(s["name"] for s in scripts))
    try:
        ctx.status("准备执行，正在关闭相关程序")
        ctx.log("关闭可能已打开的三月七、绝区零一条龙、MAA、崩坏：星穹铁道、绝区零（不关闭模拟器）…")
        close_target_apps(ctx)

        for index, script in enumerate(scripts, 1):
            ctx.check_stop()
            meta = SCRIPT_META[script["id"]]
            rec = _new_record(script)
            ctx.record(rec)
            ctx.status(f"正在运行 [{index}/{len(scripts)}] {script['name']}")
            ctx.log("-" * 50)
            ctx.log(f"[{index}/{len(scripts)}] 开始运行 {script['name']}")
            try:
                _run_one(ctx, script, meta, rec)
                ctx.status(f"{script['name']}：已完成")
            except StopRequested:
                raise
            except AutomationError:
                ctx.status(f"{script['name']}：失败")
            finally:
                rec["end"] = now_str()
                rec["duration"] = time.time() - rec.pop("_t0", time.time())
                ctx.log(f"{script['name']} 任务结束，关闭相关程序")
                time.sleep(CLOSE_GRACE)
                close_target_apps(ctx)
                if rec["state"] not in ("失败", "已停止", "已完成"):
                    rec["state"] = "未完成"
                ctx.record(rec, final=True)

            if index < len(scripts):
                ctx.check_stop()
                time.sleep(BETWEEN_SCRIPTS)

        ctx.status("空闲")
        ctx.log(f"全部任务执行结束，总耗时 {fmt_duration(time.time() - started_at)}")
    finally:
        ctx.log("清理：关闭相关程序")
        close_target_apps(ctx, quiet=True)
