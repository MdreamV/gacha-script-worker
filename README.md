# GachaScriptWorker · 二游脚本总控 — 面向 Agent 的项目说明

> 这是一份交接文档：另一个 Agent / 开发者接手本项目时，先完整读完本文，再动手修改。
> 本文描述的是 **2026-11-04** 的代码状态；如果改了机制，请同步更新本文。
> 本仓库不包含本机配置、运行历史和日志（见 `.gitignore`）；默认路径均为相对项目目录的路径。
> 远程仓库：https://github.com/MdreamV/gacha-script-worker

---

## 0. 安全红线（必读）

1. **绝对不要读取、输出、复制、截图或提交**
   `config.json` 中 `zzz` 启动器所在目录下的 `config\01\game_account.yml`。
   该文件保存了游戏账号的手机号和密码。任何时候都不需要打开它来排查问题。
2. 失败截图、运行日志、窗口截图里可能包含游戏 UID、账号信息。不要把它们上传到外部服务或写进公开文档。
3. 本项目会**真实移动鼠标、点击窗口、启动游戏并杀掉目标进程**。在用户正在使用电脑、正在玩游戏时，不要擅自运行完整任务序列；测试前先确认可以占用键鼠。
4. 三月七、绝区零一条龙的启动器都要求管理员权限。总控本身不以管理员运行也能打开，但无法正常执行这两个脚本；排障时先确认权限。
5. 新需求优先找**确定性信号**（应用日志、全局快捷键、固定颜色/位置、进程状态），不要一上来就引入 OCR。本项目已有的更新弹窗处理和一条龙启动都是这样解决的。

---

## 1. 项目概况

一个 Windows 桌面“二游脚本总控”，按顺序自动运行四个日常脚本：

| id | 名称 | 类型 | 启动对象 | 完成判定 |
| --- | --- | --- | --- | --- |
| `mihoyo` | 米游社签到 | 控制台脚本 | 在脚本目录打开 PowerShell 执行 `python main.py` | 命令回到提示符（退出码为 0），随后关闭窗口 |
| `march7th` | 三月七 | GUI 脚本 | `March7th Launcher.exe` | 游戏 `StarRail.exe` 启动并退出 |
| `zzz` | 绝区零一条龙 | GUI 脚本 | `OneDragon-Launcher.exe` | 游戏 `ZenlessZoneZero.exe` 启动并退出 |
| `maa` | 明日方舟 MAA | GUI 脚本 | `MAA.exe` | `debug/gui.log` 出现当前这轮的 `任务已全部完成！`；**不依赖模拟器关闭** |

主要能力：

- tkinter 主界面 + 系统托盘；关闭/最小化窗口会收进托盘。
- 每天定时启动（默认 08:20），也可手动“立即运行全部”。
- 执行前关闭已打开的三月七、一条龙、MAA、星铁、绝区零；**每个脚本结束后**和**整个序列结束后**再次清理。模拟器不归总控管，绝不主动关闭。
- 每个脚本记录状态、启动/完成标记、耗时、说明；说明列会写异常原因、游戏运行时长、三月七更新提示、MAA 完成用时等。
- 运行历史持久化，保留最近 100 条。
- 单实例运行（互斥锁）；非管理员启动时会提示并以管理员身份重启。
- 不使用 OCR；GUI 自动化基于相对坐标点击 + 应用日志确认 + 一条龙全局快捷键。

默认配置中 `mihoyo`、`march7th`、`zzz`、`maa` 全部启用；实际以本机 `config.json` 为准（界面可随时改启用状态与顺序），定时默认 08:20 启用。

---

## 2. 运行环境与启动方式

- Windows 10/11。当前机器为 Windows 11，Python **3.11.9**。
- 依赖见 `requirements.txt`：`pyautogui`、`psutil`、`Pillow`、`pystray`、`pywin32`。
- 安装：`pip install -r requirements.txt`。
- 启动：
  - `启动总控.bat`：优先用 `pythonw` 无控制台启动（日常使用）。
  - `调试启动.bat`：用 `python main.py`，保留控制台便于看报错。
- 必须以管理员身份运行才能真正启动三月七 / 一条龙。`main.py` 会检测 `automation.is_elevated()`，在未提权时弹窗询问是否以管理员重启。
- 当前机器已验证：通过 `Start-Process -Verb RunAs` 提权时 UAC 不会弹窗（静默提升），因此可以无人值守地跑提权测试；日志出现“当前权限：管理员”即代表提权成功。
- 普通权限下，米游社签到仍可正常运行。

路径（均相对项目目录 `worker\`）：

```
.\                         项目目录
.\logs\                    总控日志 + 失败截图
.\config.json              用户配置（路径/启用/顺序/定时，已 gitignore）
.\state.json               运行历史（已 gitignore）
```

默认目标程序路径（相对 `worker\`，以 `config.json` 为准，界面可改）：

- 米游社：`..\MihoyoBBSTools\MihoyoBBSTools_fork`（目录）
- 三月七：`..\m37\March7thAssistant_full\March7thAssistant_full\March7th Launcher.exe`
- 一条龙：`..\zzzOD\OD-2.4.7\OneDragon-Launcher.exe`
- MAA：`..\MAA\MAA.exe`

> 配置中的相对路径统一由 `config.resolve_path()` 按 `worker` 目录解析；绝对路径仍然兼容，但仓库内不要写死本机路径。

---

## 3. 文件结构与模块边界

| 文件 | 行数（参考） | 职责 |
| --- | --- | --- |
| `main.py` | ~928 | 只做界面、托盘、定时器、线程调度、配置/历史落盘；不包含窗口自动化和点击细节 |
| `automation.py` | ~1466 | 全部自动化：进程查找/关闭、窗口查找/缩放/前台、相对坐标点击、米游社控制台运行、三月七更新弹窗、一条龙 F9 启动、MAA 日志判定、游戏等待、序列流程 |
| `config.py` | ~132 | 路径常量与解析、默认配置、配置合并、JSON 原子写入、历史读写；不依赖 UI |
| `config.json` | — | 用户当前配置（脚本顺序即执行顺序） |
| `state.json` | — | 最近 100 条运行记录 |
| `logs/` | — | `worker_YYYYMMDD.log` 总控日志；`failure_<id>_<时间戳>.png` 失败截图 |
| `启动总控.bat` | — | `pythonw main.py` 日常启动 |
| `调试启动.bat` | — | `python main.py` + pause |

模块依赖方向：`main.py -> automation.py / config.py`，`automation.py -> config.py`，`config.py` 不反向依赖。新增逻辑时尽量保持这个边界：界面相关进 `main.py`，自动化进 `automation.py`。

---

## 4. 架构与线程模型

```
┌─────────────────────────── tkinter 主线程 ───────────────────────────┐
│ WorkerApp: 脚本表 / 定时器(每3秒) / 执行状态 / 运行历史 / 托盘菜单   │
│ 队列 _poll_queue（120ms）处理: log, status, record, run_done,        │
│                               show, run, stop, quit                  │
└───────────────▲───────────────────────────────┬─────────────────────┘
                │ queue.Queue                   │ 启动 daemon 线程
                │                               ▼
┌───────────────┴─────────── 工作线程 ────────────────────────────────┐
│ automation.run_sequence(ctx, scripts)                                │
│   -> close_target_apps -> 遍历脚本 -> _run_one -> ...               │
│   -> 通过 RunContext 回调把 log / status / record 放回队列          │
└───────────────┬──────────────────────────────────────────────────────┘
                │
        pyautogui / win32gui / psutil / subprocess（真实键鼠与进程操作）

pystray 托盘线程：菜单动作只往队列里放消息，不直接操作 tkinter。
```

关键点：

- `WorkerApp.stop_event` 是停止信号；`RunContext.check_stop()` 在流程的各个等待点抛出 `StopRequested`。
- `RunContext` 是自动化层与 UI 的唯一通道：`log()`、`status()`、`record()`。
- `record(rec, final=True)` 才会写入历史并持久化；非 final 记录只更新“执行状态”页签的当前值。
- 界面按脚本 id 保存最新记录 `self.latest[script_id]`，与历史里的记录相互独立。
- 定时器每 3 秒检查一次 `next_run`；触发后立即重算到第二天，所以在运行中错过一次不会补跑。
- 关闭程序时如果任务在跑，会先置停止位、等线程最多 10 秒、必要时清理目标进程，再销毁窗口。

---

## 5. 配置与持久化

### 5.1 config.json

```json
{
  "schedule": { "enabled": true, "time": "08:20" },
  "scripts": [
    { "id": "mihoyo", "name": "米游社签到", "path": "...", "enabled": true },
    { "id": "march7th", "name": "三月七", "path": "...", "enabled": false },
    { "id": "zzz", "name": "绝区零一条龙", "path": "...", "enabled": false }
  ]
}
```

- `scripts` 的**数组顺序就是执行顺序**，界面用 ▲/▼ 调整。
- `path` 的语义由 `config.py` 的 `SCRIPT_KINDS` 决定：
  - `console`（目前只有 `mihoyo`）：`path` 是**目录**，目录中执行 `automation.SCRIPT_META[id]["command"]`。
  - 其他：`path` 是**可执行文件**。
- `path` 可写绝对路径，也可写**相对 `worker\` 的路径**；运行、校验、浏览框初始化时统一走 `config.resolve_path()`，与当前工作目录无关。
- **支持的脚本 id 是硬编码的**：`config.py` 的 `SCRIPT_NAMES`。`load_config()` 会丢弃未知 id，并把缺失的默认脚本按默认位置补回来。新增脚本必须同时改 `config.py`，否则保存后的配置项会在下次加载时消失。
- 保存是原子写：先写临时文件再 `os.replace`。配置损坏时会改名为 `config.json.bad` 并回退默认配置。
- 手动运行前会自动保存一次界面配置；点“保存配置”或退出也会保存。只改定时时间但既不保存也不运行就退出，修改不会持久化（正常退出会保存）。

### 5.2 state.json / 运行记录

```json
{ "records": [ { "rid": "...", "id": "march7th", "name": "三月七",
  "start": "...", "end": "...", "duration": 123.4,
  "launched": true, "completed": true, "state": "已完成",
  "note": "..." } ] }
```

- 最多 100 条，新的在前。
- `rid`：单次运行 id；`launched`：是否成功启动脚本；`completed`：是否正常完成（失败/停止为 false）；`duration` 秒；`note` 给用户看的说明。
- 状态值包括：`准备中`、`启动脚本`、`等待执行完成`、`等待游戏启动`、`游戏运行中`、`已完成`、`失败`、`已停止`、`未完成`。
- `_t0`（内部计时）和 `final` 不会持久化。
- 不要手工改 `state.json` 来“修状态”；要查真实过程看 `logs/worker_YYYYMMDD.log`。

---

## 6. 核心执行流程

入口：`automation.run_sequence()`（automation.py:1425）。

1. 记录序列开始；`close_target_apps()` 关闭三月七 / 一条龙 / 星铁 / 绝区零及相关进程。
2. 按 UI 顺序遍历**启用**的脚本：
   - 新建记录并推送到 UI。
   - `_run_one()` 执行该脚本。
   - 无论成功、失败还是停止，`finally` 中会：写结束时间/耗时 → 等 5 秒 → 再次 `close_target_apps()` → 推最终记录。
   - 除 `StopRequested` 外，单个脚本失败不会中断后续脚本。
3. 两个脚本之间等待 10 秒。
4. 序列结束把状态设为“空闲”；最外层 `finally` 再静默清理一次目标进程。

`_run_one()` 的通用 GUI 流程（automation.py 中的 `_run_one`）：

```
校验 exe 路径
-> 记录三月七当天日志 offset（仅 update_prompt 脚本）
-> 记录 MAA debug/gui.log 当前末尾 offset（仅 finish_log 脚本）
-> [MAA] 写 config/gui.new.json，把设置指引标记为已完成
-> launch_script
-> wait_for_window（最多 600 秒；MAA 管理员运行时标题是「MAA (管理员) - …」）
-> resize_client 到固定客户区尺寸
-> ensure_foreground + settle
-> [MAA] 等 Init 日志，确认界面可点击
-> [MAA] 等 MAA 自己的“启动后自动开启模拟器”阶段结束（最多 75 秒）
-> [三月七] 处理更新弹窗，生成 update_note
-> 按 click_steps 点击 / 或点击后用日志确认开始（MAA）
-> [一条龙] 按全局 F9，并用日志确认开始
-> [MAA] 只读日志等 `任务已全部完成！`，不等待游戏/模拟器进程
-> 其他脚本：wait_game_start（最多 30 分钟）-> wait_game_exit（最多 6 小时）
-> completed = True，note 记录游戏运行时长或 MAA 用时
```

失败时：

- `AutomationError` 和未知异常都会走 `_mark_failure()`，状态置“失败”，并在窗口仍存在时保存失败截图。
- 如果此前已经识别到三月七更新，失败说明前面会拼上 `update_note`。
- 超时/停止也是失败或“已停止”；停止时 PowerShell 窗口按设计保持打开。

主要超时/间隔常量在 `automation.py` 顶部：

- `WINDOW_TIMEOUT = 600`
- `GAME_START_TIMEOUT = 30 分钟`
- `GAME_RUN_TIMEOUT = 6 小时`
- `SHORT_RUN_WARN = 180`（游戏运行过短会在说明里提醒）
- `CLOSE_GRACE = 5`、`BETWEEN_SCRIPTS = 10`
- `CONSOLE_RUN_TIMEOUT = 30 分钟`
- `UPDATE_CHECK_WAIT = 30`、`UPDATE_DIALOG_WAIT = 8`
- `ONEDRAGON_START_WAIT = 15`
- MAA：`MAA_READY_WAIT = 90`、`MAA_AUTOSTART_PROBE = 15`、`MAA_AUTOSTART_WAIT = 75`、`MAA_START_WAIT = 20`、`MAA_CLICK_RETRY_WAIT = 6`

---

## 7. 四个脚本的具体机制

### 7.1 米游社签到（console）

实现：`automation._run_console()`（automation.py:396）。

- 在脚本目录中启动：
  `powershell.exe -NoProfile -NoLogo -NoExit -Command "..."`
- 通过随机 token 设置窗口标题 `<名称> <token>`，并在 `%TEMP%` 下创建 `<token>.done` 标记文件。
- PowerShell 包装命令：
  - `Set-Location` 到脚本目录；
  - 执行 `SCRIPT_META["mihoyo"]["command"]`（当前 `python main.py`）；
  - 把 `$LASTEXITCODE`（异常时为 -998/-999）写入 marker 文件。
- Python 每秒检查 marker 是否存在，最多 30 分钟：
  - marker 出现且退出码为 0：判定完成，关闭该 PowerShell 窗口；
  - 退出码非 0：判定失败，**保留窗口**方便用户看原因；
  - PowerShell 进程先退出（用户关窗）：失败；
  - 超时：失败，窗口保留。
- 之前的方案是识别 PowerShell 提示符，已废弃；marker 文件更可靠。
- 界面“打开”按钮调用 `open_script_manually()`：同样打开 PowerShell 并执行 `python main.py`，但不写 marker、不自动关闭，供用户手动确认。

### 7.2 三月七（GUI）

元数据：`SCRIPT_META["march7th"]`（automation.py:72）。

- 窗口：标题包含 `March7th Assistant`；最小宽度 700；优先匹配进程 `march7th launcher.exe` / `march7th assistant.exe`。
- 客户区固定为 **960×640**，等待 1.5 秒让界面稳定。
- 点击步骤：主页“任务”区域第一张卡片 **完整运行**，相对坐标 `(0.16354, 0.78125)`。
- 游戏进程：`StarRail.exe`。

#### 更新弹窗（重点）

三月七启动时可能提示新版本，界面里是嵌入主窗口的模态层，需要点“好的”跳过更新。实现不依赖 OCR：

1. **是否有新版本**：启动前记录当天日志 `logs/YYYY-MM-DD.log` 的文件大小；启动后只读新增内容。
   - 新版本：`GitHub 发现新版本: v2026.10.31`（正则 `UPDATE_FOUND_RE`）。
   - 无更新：`已经是最新版本` / `确认已是最新版本` / `检测更新失败`，直接返回，不点任何东西。
   - 弹窗标题行格式为 `发现新版本：v2026.10.16  ——→  v2026.10.31`，**第一个版本是当前版本，必须取箭头后面的版本**；正则为 `UPDATE_DIALOG_TITLE_RE`。
2. **弹窗是否存在**：截图主窗口，在窗口高度的 40%~97% 区域寻找连续 ≥180 像素的粉色横条（“手动下载”按钮），颜色条件为红高、绿中等、蓝偏高。找到后取同高度的“好的”按钮：它与“手动下载”等宽，位于粉色横条左侧约一个按钮宽度 + 12px 间隔处。
3. 点击“好的”，等 1.2 秒重新截图确认弹窗消失；失败最多重试 2 次，仍失败则抛错“三月七更新提示窗口未能关闭，请手动点击「好的」后重试”。
4. 返回给说明列的文本：
   - `三月七有可用更新（v2026.10.31），已点击「好的」跳过更新`
   - 有版本但没等到窗口：`三月七有可用更新（v…），未弹出更新窗口`
   - 无更新：空字符串。

已知约束/坑：

- **不要试图从 Win32 窗口标题判断更新**：标题始终是 `March7th Assistant`，更新角标是自绘控件，不在 `GetWindowText` 里。
- 需要 `config.yaml` 中 `check_update: true`（当前为 true）。如果用户关掉更新检查，日志不会出现上述任何标记，`_wait_update_prompt()` 会空等满 30 秒后才继续。
- 日志路径固定在 exe 同级的 `logs/YYYY-MM-DD.log`；应用若改日志结构，需要同步改 `_run_one()` 里的 `update_log` 路径和正则。
- 粉色按钮阈值是针对当前主题调的；三月七换主题/改按钮布局后，用失败截图重新校准 `_find_update_ok_button()`。
- 弹窗关闭后会重新切前台并等 0.5 秒，再执行“完整运行”点击。

### 7.3 绝区零一条龙（GUI）

元数据：`SCRIPT_META["zzz"]`（automation.py:85）。

- 窗口：标题包含 `一条龙`，排除含 `启动器` 的窗口；最小宽度 800；优先匹配一条龙的启动器进程；另外会用可见 Qt 窗口标题兜底识别（`_target_window_pids()`）。
- 客户区固定为 **1140×786**。注意：一条龙自身的最小高度就是 786，设成 747 也仍是 786，旧坐标因此点不到按钮。现在统一按 1140×786 校准。
- 点击步骤：左侧菜单 **一条龙**，相对坐标 `(0.05263, 0.26774)`，点击后等 4 秒让“开始 F9”页渲染。
- 不再点击“开始 F9”按钮，改为：
  1. 确保一条龙窗口前台；
  2. `pyautogui.press("f9")` 发送全局快捷键；
  3. 从 `.log/log.txt` 的当前末尾开始轮询，最多 15 秒，出现 `已启用前台模式` 或 `已启用后台模式` 即确认开始；
  4. 若 15 秒没有标记：重新点一次左侧“一条龙”菜单，等 2 秒，再按一次 F9、再等 15 秒；
  5. 仍失败：抛错“按 F9 后一条龙仍未开始运行，请检查一条龙窗口状态”；
  6. 日志不可读：无法确认，但已按 F9，只记提示并继续等游戏进程。
- 游戏进程：`ZenlessZoneZero.exe`。

为什么这样设计：

- 一条龙窗口高度受自身最小尺寸约束，按钮坐标容易偏；F9 是它自带的全局快捷键，不依赖按钮坐标。
- 日志标记来自一条龙的 `init_before_context_run()`，能区分“键确实被接收并开始运行”与“只是按了键”。
- F9 只会在“一条龙”页注册了页面级监听后才生效，所以必须先点左侧菜单；在一条龙**已经运行**时 F9 是暂停/继续，不是开始。总控每次都会先关闭旧的一条龙再启动新的，保证处于 STOP 状态。
- 默认快捷键来自 `env_config.key_start_running` 默认值 `f9`；如果用户在一条龙设置里改了快捷键，需要同步修改 `SCRIPT_META["zzz"]["start_hotkey"]`。
- 日志路径目前是 `应用目录\.log\log.txt`。一条龙升级后若改路径或标记文字，需要同步更新。

### 7.4 明日方舟 MAA（GUI，模拟器由 MAA 管理）

元数据：`SCRIPT_META["maa"]`。

与其他脚本的关键区别：

- MAA 通过模拟器运行游戏。总控**不检查模拟器是否已运行、不关闭模拟器、不以模拟器退出作为完成条件**。`TARGET_PROCESS_NAMES` 只包含 `maa.exe`，绝不把 MuMu / 雷电 / BlueStacks 等模拟器进程加进去。
- 窗口标题：一般是 `MAA - …`；**以管理员身份运行时变成 `MAA (管理员) - …`**，所以 meta 用 `window_titles` 同时匹配两种前缀。
- 客户区固定 **800×600**；启动按钮是左下角 `Link Start!`，相对坐标 `(0.18375, 0.89833)`。
- 窗口出现早于界面可点击，需等日志 `Init: false to true`（`ready_log_marker`），最多 90 秒。
- **设置指引**：新版 MAA 首次启动会显示 7 步“设置指引”，此时没有 `Link Start!`。总控在启动前把 `config/gui.new.json` 的 `Gui.GuideStep` 写成 7、`Gui.GuideTutorialVersion` 写成 2（等价于界面的 `DoneGuide()`）。写入时 MAA 已经被关闭；写失败只记提示，不阻塞。
- **自动启动模拟器**：如果 MAA 开启了“启动 MAA 后自动开启模拟器”，启动约 10 秒后日志出现 `Try to start emulator` / `Waiting for the emulator to start: 60s`，最长等 60 秒。**这个阶段左下角按钮实际是“停止”**，提前点击会写入 `Stop waiting for the emulator to start`，任务无法开始。总控读日志等到 `Waiting for the emulator is over` / `等待结束` 后才点击；15 秒内没有进入该流程则认为未启用，直接点击。
- **更新弹窗**：标题含“新版本”等关键字；对弹窗发 `WM_CLOSE` 等价于点“稍后”。在等就绪、等模拟器、点击、等完成时都会顺带检查并关闭。
- **开始确认**：点击后轮询日志 `LinkStart Enter`；若先出现 `BeginRun: owner=` 或 `Idle: true to false (called from BeginRun)` 也算已开始，避免把已经运行的 MAA 再点成“停止”。首次 6 秒没看到标记会重试一次，再等 20 秒。
- **完成判定**：只读 `debug/gui.log` 的新增部分（启动前记录 offset；文件变小则从头读；带时间戳过滤旧内容），出现当前这轮的 `任务已全部完成！` 即成功。**不等待游戏/模拟器进程，也不关闭模拟器**。
  - 注意：如果 MAA 自己的“完成后动作”里勾了“退出模拟器”，完成后是 **MAA** 去关模拟器（日志 `Post actions: 退出 模拟器`），总控不参与也不等待；看到模拟器被关不要误以为是总控干的，更不要把模拟器加进清理列表。
- **失败判定**：同一轮日志出现 `任务出错:` / `连接失败` / `已停止` 时提前失败，不会空等 6 小时。
- **配置写入安全**：关闭 MAA 时先发 `WM_CLOSE` 等最多 3 秒，再强制结束，降低打断 `gui.new.json` 写入导致配置损坏的概率。
- **截图限制**：MAA 是 WPF 应用，本机的 pyautogui / PrintWindow 截图是白屏；失败截图对 MAA 没有诊断价值。调试请用 UIA（pywinauto，仅限诊断）和 `debug/gui.log`。

MAA 日志很大且持续增长，必须用 `_read_log_after()` 从 offset 增量读取；不要整文件 `read_text()`。

---

## 8. 关键函数索引（automation.py）

行号基于 2026-11-03 版本，改动后会漂移，以函数名为准。

| 功能 | 函数 | 位置 |
| --- | --- | --- |
| 关闭所有目标进程 | `close_target_apps` | :321 |
| 先正常关闭 MAA | `_request_maa_close` | :295 |
| 管理员判断 | `is_elevated` | :409 |
| 启动 exe | `launch_script` | :416 |
| 手动打开脚本 | `open_script_manually` | :449 |
| 米游社控制台流程 | `_run_console` | :476 |
| 查找脚本窗口 | `find_script_window` | :588 |
| 调整客户区尺寸 | `resize_client` | :642 |
| 切前台 | `ensure_foreground` | :670 |
| 相对坐标点击 | `click_client` | :716 |
| 解析更新版本 | `_parse_update_version` | :733 |
| 截图窗口区域 | `_capture_window` | :738 |
| 定位“好的”按钮 | `_find_update_ok_button` | :750 |
| 关闭更新弹窗 | `_dismiss_update_dialog` | :784 |
| 等待/处理更新提示 | `_wait_update_prompt` | :805 |
| 读日志增量（三月七） | `_read_log_since` | :864 |
| 按快捷键并看日志确认 | `_press_hotkey_and_check` | :873 |
| 一条龙 F9 启动 | `_start_onedragon` | :902 |
| 读日志增量（MAA，offset） | `_read_log_after` | :925 |
| 按时间戳匹配 MAA 日志 | `_find_log_marker` | :939 |
| 标记 MAA 设置指引完成 | `_mark_maa_guide_done` | :955 |
| 关闭 MAA 更新提示 | `_dismiss_maa_dialogs` | :990 |
| 等 MAA 界面就绪 | `_wait_maa_ready` | :1025 |
| 等 MAA 自动启动模拟器结束 | `_wait_maa_autostart` | :1046 |
| 点击 Link Start! 并确认开始 | `_start_maa` | :1090 |
| 等 MAA 完成/失败日志 | `_wait_maa_completion` | :1121 |
| 等待游戏启动 | `wait_game_start` | :1149 |
| 等待游戏退出 | `wait_game_exit` | :1173 |
| 单个脚本主流程 | `_run_one` | :1227 |
| 序列主流程 | `run_sequence` | :1424 |

---

## 9. 校准与扩展指南

### 9.1 重新校准点击坐标

前提：先把目标窗口调整到 `SCRIPT_META` 里的固定客户区尺寸，并切到前台。用下面的脚本截取客户区并记录鼠标位置：

```python
import pyautogui
import win32gui

hwnd = 0  # 用 win32gui.FindWindow(None, "窗口标题") 或自行枚举
left, top = win32gui.ClientToScreen(hwnd, (0, 0))
l, t, r, b = win32gui.GetClientRect(hwnd)
cw, ch = r - l, b - t
img = pyautogui.screenshot(region=(left, top, cw, ch))
img.save("calibration.png")
print("client size:", cw, ch)
print("mouse screen pos:", pyautogui.position())
```

把鼠标放到目标按钮中心，读取 `pyautogui.position()`，则：

```
fx = (mouse_x - left) / cw
fy = (mouse_y - top) / ch
```

`click_steps` 每项是 `(说明, fx, fy, 点击后等待秒数)`。点击是“客户区相对坐标”，所以窗口位置随便移动不影响；但**客户区尺寸必须和校准一致**。现有固定尺寸：三月七 960×640，一条龙 1140×786，MAA 800×600。

> MAA 的 `Link Start!` 相对坐标是 `(0.18375, 0.89833)`（800×600 客户区内约为 `(147, 539)`）。MAA 是 WPF 自绘界面，截图会白屏，校准要用 UIA 拿控件矩形，不要靠截图找按钮。

### 9.2 新增一个脚本

1. `config.py`：在 `SCRIPT_NAMES` 加 id/显示名；在 `default_config()["scripts"]` 加默认路径和启用状态。控制台脚本还要在 `SCRIPT_KINDS` 标为 `"console"`。
2. `automation.py`：在 `SCRIPT_META` 新增该 id 的元数据。
   - 控制台脚本：`kind: "console"`、`title`、`command`、`timeout`，不需要窗口信息。
   - GUI 脚本：`window_title`（或 `window_titles`）、`exclude_titles`、`window_processes`、`min_window_width`、`client_size`、`settle_after_window`、`click_steps`、`game_process`、`game_name`；可选 `update_prompt`、`start_hotkey`、`ready_log_marker`、`wait_autostart`、`finish_log`、`finish_marker`、`finish_failure_markers`。
3. 如果是新应用/新游戏，把启动器和游戏进程名加入 `TARGET_PROCESS_NAMES`（必要时补 `TARGET_PATH_KEYS`、`TARGET_CMD_KEYS`），否则开始前/结束后不会清理它。**模拟器进程不要加**。
4. 如果新 GUI 脚本**没有游戏进程**，现有 `_run_one()` 的“等游戏启动/退出”完成判定不适用，需要使用 `finish_log` + `finish_marker`（MAA 就是这样）或新增一种完成策略，不要硬塞一个假游戏进程。
5. 改完先 `python -m py_compile main.py automation.py config.py`，再单独启用新脚本测试。

> 注意：`load_config()` 只认 `SCRIPT_NAMES` 里的 id，漏改 `config.py` 时，界面保存的配置在重启后会丢失。

### 9.3 应用升级后的检查清单

- 窗口标题、进程名是否变化（`find_script_window` / `TARGET_PROCESS_NAMES`）。
- 客户区尺寸是否仍可调整到元数据值；一条龙尤其注意最小高度。
- 左侧菜单/按钮位置是否变化；重新校准 `click_steps`。
- 三月七：日志路径、更新日志关键词、弹窗粉色按钮位置/颜色。
- 一条龙：F9 默认值是否被改、`.log/log.txt` 路径、启动成功日志文字。
- MAA：标题是否仍带 `MAA -` / `MAA (管理员) -`；`debug/gui.log` 路径；`Init: false to true`、`LinkStart Enter`、`任务已全部完成！`、`Try to start emulator` / `Waiting for the emulator is over` 等标记；`config/gui.new.json` 的指引字段是否改名。
- 重启总控（`pythonw` 不会热加载代码）。

---

## 10. 测试、排障与清理

### 10.1 基本检查

```bat
cd /d "%~dp0"
python -m py_compile main.py automation.py config.py
```

- 用 `调试启动.bat` 启动可以看到异常堆栈。
- 日志：`logs/worker_YYYYMMDD.log`；界面状态页签显示的是内存中的最新记录。
- 失败截图：`logs/failure_<id>_<YYYYmmdd_HHMMSS>.png`（仅当失败时窗口还存在）。
- 提权测试：PowerShell 里 `Start-Process -FilePath 'python' -ArgumentList '...' -Verb RunAs`；日志确认“当前权限：管理员”。当前机器的提权是静默的。

### 10.2 测试注意

- 测试会真实启动应用、点击和启动游戏。先确认用户不在玩；测试单个脚本时在界面里关闭其他脚本，避免整套运行。
- 一条龙测试如果手动停止，可切到其窗口按 F10（人工结束），再确认没有残留游戏进程；必要时用 `close_target_apps()` 或任务管理器清理。
- 三月七更新弹窗测试会真正点击“好的”；确认当前确实有更新提示，否则函数会等满 30 秒。
- MAA 测试会真实启动模拟器和明日方舟。**模拟器不归总控关闭**，测试结束后由用户自行决定是否关闭；不要为了“清理干净”去杀 MuMu / 雷电等模拟器进程。
- MAA 以管理员运行时窗口标题会变成 `MAA (管理员) - …`，测试时确认 `window_titles` 能匹配；截图白屏是 WPF 限制，不是失败原因。
- 测试结束后检查：`StarRail.exe`、`ZenlessZoneZero.exe`、`March7th*`、`OneDragon*`、`MAA.exe` 是否残留；模拟器可以保持运行。不要留下正在运行的游戏或脚本。
- 不要删除用户正在使用的 `config.json` / `state.json`；测试可以临时修改配置，但结束后恢复。

### 10.3 常见故障对照

| 现象 / 报错 | 可能原因 | 处理 |
| --- | --- | --- |
| “等待脚本窗口超时” | 未提权、启动器路径错、窗口标题改了、启动器弹了登录/错误框 | 确认管理员、路径、窗口标题；手动打开看是否卡在启动器 |
| “脚本窗口已关闭，游戏未启动” | 点击没命中、应用报错、更新弹窗没关掉 | 看失败截图和总控日志，重新校准点击/更新处理 |
| “按 F9 后一条龙仍未开始运行” | 快捷键被用户改掉、一条龙页没显示、窗口没抢到前台、日志路径/文字变了 | 核对 `start_hotkey`、手动看页面、检查 `.log/log.txt` 新内容 |
| “三月七更新提示窗口未能关闭” | 主题/布局改变导致粉色锚点或按钮位置失效 | 用失败截图重新校准 `_find_update_ok_button()`，或临时手动点 |
| 三月七每次启动都要等 30 秒 | `check_update` 被关闭 / 日志没有“已是最新版本”标记 | 恢复更新检查，或调整 `_wait_update_prompt()` 的等待策略 |
| “等待脚本窗口超时”，但 MAA 明明开着 | MAA 以管理员运行时标题是 `MAA (管理员) - …` | 确认 `window_titles` 包含 `MAA (管理员) -` |
| “点击 Link Start! 后 MAA 未开始运行” | 点在了自动启动模拟器的“停止”阶段 / 设置指引没关 / 窗口没前台 | 看 `debug/gui.log` 是否出现 `Stop waiting for the emulator to start`；确认 `GuideStep=7`；确认日志有 `LinkStart Enter` |
| “等待 MAA 自动启动模拟器超时” | MAA 自动启动模拟器超过 75 秒，或日志标记变了 | 看 gui.log 的 `Waiting for the emulator…` 行；确认模拟器本身能启动；必要时调整 `MAA_AUTOSTART_WAIT` |
| “MAA 日志显示任务未完成（连接失败）” | 模拟器未就绪 / ADB 端口不对 / 游戏未安装 | 先手动点一次 Link Start! 确认能连接，再查 MAA 连接设置 |
| MAA 日志文件很大 | 正常现象，gui.log 会一直增长 | 总控只用 offset 增量读取，不要手动截断正在使用的日志 |
| “该程序要求管理员权限” | 总控未提权 | 以管理员身份运行总控 |
| “警告：以下进程未能关闭” | 权限不足或进程卡死 | 用管理员运行；必要时手动结束后重试 |
| 点击位置总是偏 | 客户区尺寸和校准时不一致 / DPI 缩放 | 看日志里的“窗口客户区为 …”提示；重新校准尺寸；程序已启用 Per-Monitor DPI 感知 |
| 修改代码后行为没变 | 旧的总控进程还在运行（pythonw） | 退出托盘里的总控并重新启动 `启动总控.bat` |

---

## 11. 已知设计与坑（改代码前先看）

1. **无边框 Qt 窗口**：三月七、一条龙都是自绘标题栏，`GetWindowRect` 基本等于客户区。`resize_client()` 仍按“外框 - 客户区”的差值换算，兼容有边框窗口。不要用固定的边框像素假设。
2. **真实键鼠输入**：使用 pyautogui 全局点击，不是 PostMessage/后台模式。目标窗口必须前台；用户移动鼠标会干扰。`pyautogui.FAILSAFE = False`，鼠标甩到角落不会中止。
3. **切前台的两段策略**：先 `AttachThreadInput` + `SetForegroundWindow`，失败后点一次窗口顶部中间（标题栏区域）抢焦点；再失败会记警告，但流程继续，可能点不中。
4. **一条龙全局 F9 的副作用**：F9 在运行中是暂停/继续。当前重试逻辑在“15 秒没看到日志标记”后才会重按；如果日志标记延迟或路径变了而实际已经开始，第二次 F9 可能把一条龙暂停。升级后务必确认标记文字和日志路径。
5. **三月七更新检测顺序**：`_wait_update_prompt()` 先看弹窗像素，再看日志；有更新时不必等日志写完；有日志版本但窗口渲染慢时最多再等 8 秒。日志 offset 在启动前记录，避免读到旧内容。
6. **日期跨天**：三月七日志按本地日期命名，offset 在启动前取。08:20 定时运行不会跨天；如果深夜手动运行跨过零点，日志文件会换成新文件，读取偏移需要重新确认（当前实现不特殊处理）。
7. **进程清理范围很宽**：会按进程名、exe 路径关键字、命令行关键字和 Qt 窗口标题匹配三月七/一条龙/MAA，并递归杀子进程；同时保护总控自身及其父进程链。**模拟器不在清理范围**，这是 MAA 的明确要求。不要在目标应用里跑用户其他重要 python 任务，它们可能被一起清理。
8. **一条龙运行时由 uv/python 组成**：`TARGET_CMD_KEYS` 只对 `python.exe/pythonw.exe/uv.exe/uvx.exe` 做命令行匹配，避免误杀普通终端/编辑器；新增一条龙版本时先确认命令行结构。
9. **停止不是即时中断**：只在等待点/每次点击前检查 `stop_event`，正在进行的 pyautogui 点击无法中断；停止后由 finally 清理目标进程。
10. **控制台脚本失败保留窗口**：这是用户明确要求，便于排查；成功才关窗。不要改成一律关闭。
11. **托盘依赖 pystray**：缺失时程序仍能运行，只是关闭窗口会退出；退出任务前会二次确认。
12. **当前目录没有 git**：修改前自行备份；不要把账号配置、日志、截图提交或外传。
13. **MAA 自动启动模拟器期间不能点 Link Start!**：该阶段按钮是“停止”。必须等 `Waiting for the emulator is over` / `等待结束` 之后点击；总控的 `_wait_maa_autostart()` 已处理，不要删掉。
14. **MAA 设置指引**：新版首次启动的 7 步指引会挡住 Link Start!，且“下一步”在未完成前置步骤时是禁用的。总控通过 `gui.new.json` 的 `GuideStep=7` / `GuideTutorialVersion=2` 直接标记完成；MAA 升级可能重置这两个值，助手会在每次运行前重新写入。
15. **MAA 配置写入**：`close_target_apps()` 对 `MAA.exe` 先发 `WM_CLOSE` 等 3 秒再强杀，避免打断 `gui.new.json` 写入。之前强制结束曾导致一次 “Failed to load configuration file and/or backup file” 警告。
16. **MAA 截图白屏**：WPF 自绘窗口在 pyautogui / PrintWindow 下截不到内容；失败截图对 MAA 无诊断价值。调试用 UIA 控件树和 `debug/gui.log`，不要据此判断窗口没渲染。
17. **MAA 日志有旧标记**：`_find_log_marker()` 用时间戳和启动前 offset 双重过滤；不要只用字符串 `in` 判断，否则会把上一轮的 `任务已全部完成！` 当成本轮完成。

---

## 12. 交接速查

接手后的顺序建议：

1. 读第 0 节安全红线；不要打开 `game_account.yml`。
2. 看 `config.json` 确认真实路径、启用状态、定时时间；看 `logs/worker_*.log` 最近一次运行结果。
3. 改代码前 `python -m py_compile main.py automation.py config.py` 建立基线。
4. 只测一个脚本时，在界面里关闭其他脚本；确认当前没有游戏/脚本在跑。
5. 修改机制后同步更新本文；尤其更新第 8 节行号、第 7 节机制描述。
6. 结束前确认：无残留游戏进程，托盘里的旧总控已退出，用户下次用 `启动总控.bat` 启动的是新代码。

截至 2026-11-03 的验证状态：

- 三月七更新弹窗（v2026.10.31）已实测：识别版本 → 点“好的” → 弹窗消失 → 说明列写入更新提示。
- 一条龙 F9 启动已实测：点“一条龙”菜单后按 F9，0.4 秒内日志出现“已启用前台模式”，确认真实开始；测试后已用 F10 停止，无游戏残留。
- MAA（v6.21.2）已实测通过：等待 Init → 等待自动启动模拟器结束 → 点 Link Start! → 日志 `LinkStart Enter` → 等 `任务已全部完成！` → 记录用时；全程未关闭模拟器。
- MAA 的 `MAA (管理员) -` 标题、设置指引（GuideStep=7）、更新弹窗 `WM_CLOSE`=稍后，均已在实机验证。
- 这些修复提交后，必须重启总控才能生效。
