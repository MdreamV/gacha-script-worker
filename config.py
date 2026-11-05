from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# 打包成 exe 后，配置和日志放在 exe 所在目录；源码运行时放在项目目录
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"

# 相对路径以本项目目录（worker）为基准，便于仓库整体迁移
MIHOYO_PATH = r"..\MihoyoBBSTools\MihoyoBBSTools_fork"
MARCH7TH_PATH = r"..\m37\March7thAssistant_full\March7thAssistant_full\March7th Launcher.exe"
ZZZ_PATH = r"..\zzzOD\OD-2.4.7\OneDragon-Launcher.exe"
MAA_PATH = r"..\MAA\MAA.exe"

SCRIPT_NAMES = {
    "mihoyo": "米游社签到",
    "march7th": "三月七",
    "zzz": "绝区零一条龙",
    "maa": "明日方舟 MAA",
}
# console：路径是脚本目录，在该目录的 PowerShell 窗口中执行命令
SCRIPT_KINDS = {"mihoyo": "console"}


def resolve_path(path: str) -> str:
    text = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    if not text:
        return ""
    if os.path.isabs(text):
        return os.path.normpath(text)
    return os.path.normpath(os.path.join(str(APP_DIR), text))


def default_config() -> dict:
    return {
        "schedule": {"enabled": True, "time": "08:20"},
        "scripts": [
            {"id": "mihoyo", "name": SCRIPT_NAMES["mihoyo"], "path": MIHOYO_PATH, "enabled": True},
            {"id": "march7th", "name": SCRIPT_NAMES["march7th"], "path": MARCH7TH_PATH, "enabled": True},
            {"id": "zzz", "name": SCRIPT_NAMES["zzz"], "path": ZZZ_PATH, "enabled": True},
            {"id": "maa", "name": SCRIPT_NAMES["maa"], "path": MAA_PATH, "enabled": True},
        ],
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_config() -> dict:
    cfg = default_config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        save_config(cfg)
        return cfg
    except (OSError, ValueError):
        if CONFIG_PATH.exists():
            try:
                CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".json.bad"))
            except OSError:
                pass
        save_config(cfg)
        return cfg

    schedule = raw.get("schedule") if isinstance(raw, dict) else None
    if isinstance(schedule, dict):
        cfg["schedule"]["enabled"] = bool(schedule.get("enabled", True))
        time_text = schedule.get("time")
        if isinstance(time_text, str) and time_text.strip():
            cfg["schedule"]["time"] = time_text.strip()

    saved_scripts = raw.get("scripts") if isinstance(raw, dict) else None
    if isinstance(saved_scripts, list):
        by_id = {}
        for item in saved_scripts:
            if isinstance(item, dict) and item.get("id") in SCRIPT_NAMES:
                by_id[item["id"]] = item
        ordered = []
        for item in saved_scripts:
            sid = item.get("id") if isinstance(item, dict) else None
            if sid in by_id and sid not in [s["id"] for s in ordered]:
                default_item = next(s for s in cfg["scripts"] if s["id"] == sid)
                path = by_id[sid].get("path")
                ordered.append(
                    {
                        "id": sid,
                        "name": default_item["name"],
                        "path": path.strip() if isinstance(path, str) and path.strip() else default_item["path"],
                        "enabled": bool(by_id[sid].get("enabled", True)),
                    }
                )
        for default_index, default_item in enumerate(cfg["scripts"]):
            if default_item["id"] not in [s["id"] for s in ordered]:
                # 新版本新增的脚本按默认顺序插入（例如米游社签到默认第一）
                ordered.insert(min(default_index, len(ordered)), default_item)
        if ordered:
            cfg["scripts"] = ordered
    return cfg


def save_config(cfg: dict) -> None:
    _write_json(CONFIG_PATH, cfg)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return {"records": data["records"][:100]}
    except (OSError, ValueError):
        pass
    return {"records": []}


def save_state(state: dict) -> None:
    _write_json(STATE_PATH, {"records": state.get("records", [])[:100]})
