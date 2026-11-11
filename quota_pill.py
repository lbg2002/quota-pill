#!/usr/bin/python3
import os
import sys

# 必须在导入任何 gi / Gtk 模块之前设置 GDK_BACKEND，确保在 Wayland 下走 XWayland 实现可靠置顶与拖拽
os.environ["GDK_BACKEND"] = "x11"
os.environ["PATH"] = f"{os.path.expanduser('~/.local/bin')}:/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"

import re
import json
import math
import time
import fcntl
import signal
import tempfile
import subprocess
import threading
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo

CONFIG_DIR = os.path.expanduser("~/.config/quota-pill")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CACHE_DIR = os.path.expanduser("~/.cache/quota-pill")
LOG_PATH = os.path.join(CACHE_DIR, "quota-pill.log")
PID_PATH = os.path.join(CACHE_DIR, "quota-pill.pid")
DATA_CACHE_PATH = os.path.join(CACHE_DIR, "last_quotas.json")

PRESET_COLORS = {
    "翠绿 (Mint)": "#0de085",
    "霓虹绿 (Neon)": "#22c55e",
    "竹青 (Bamboo)": "#789262",
    "青蓝 (Cyan)": "#00b4d8",
    "天青 (Celadon)": "#5aa4ae",
    "天空蓝 (Sky)": "#38bdf8",
    "霁蓝 (Sapphire)": "#3b82f6",
    "靛青 (Indigo)": "#6366f1",
    "紫罗兰 (Purple)": "#a855f7",
    "霓虹洋红 (Magenta)": "#d946ef",
    "樱花粉 (Sakura)": "#f472b6",
    "珊瑚红 (Rose)": "#f43f5e",
    "朱砂红 (Cinnabar)": "#ef4444",
    "琥珀橙 (Amber)": "#f59e0b",
    "松花黄 (Gold)": "#eab308",
    "极光白 (White)": "#f8fafc"
}

OPACITY_PRESETS = [
    (1.00, "100% (完全不透明)"),
    (0.92, "92% (默认深邃)"),
    (0.82, "82% (轻度通透)"),
    (0.70, "70% (中度半透)"),
    (0.55, "55% (高透毛玻璃感)"),
    (0.35, "35% (极简微透)")
]

DEFAULT_CONFIG = {
    "enabled": {"agy_g": True, "agy_c": True, "codex": True},
    "colors": {"agy_g": "#0de085", "agy_c": "#a855f7", "codex": "#00b4d8"},
    "low_quota_warn": True,
    "layout": "vertical",     # "topbar" | "columns" | "horizontal" | "vertical"
    "scale": "compact",       # "compact" | "mini"
    "pill_opacity": 0.90,
    "tip_opacity": 0.94,
    "time_format": "relative", # "relative" (4h 30m) | "abs_hm" (02:30) | "abs_smart" (跨天带日期)
    "interval": 60,
    "timeout": 15,
    "always_on_top": True,
    "pos_x": None,
    "pos_y": None
}

PROVIDER_META = {
    "agy_g": {"name": "Antigravity · Gemini", "short": "AGY·G", "badge": "G"},
    "agy_c": {"name": "Antigravity · Claude/GPT", "short": "AGY·C", "badge": "C"},
    "codex": {"name": "OpenAI Codex", "short": "Codex", "badge": "Cx"}
}


def log_msg(level, msg):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 102400:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-200:]
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{level}] {msg}\n")
    except Exception:
        pass


def validate_and_load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not os.path.exists(CONFIG_PATH):
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return cfg

        if isinstance(raw.get("enabled"), dict):
            for k in cfg["enabled"]:
                if isinstance(raw["enabled"].get(k), bool):
                    cfg["enabled"][k] = raw["enabled"][k]
            if not any(cfg["enabled"].values()):
                cfg["enabled"]["agy_g"] = True

        if isinstance(raw.get("colors"), dict):
            for k in cfg["colors"]:
                val = raw["colors"].get(k)
                if isinstance(val, str) and re.match(r"^#[0-9a-fA-F]{6}$", val):
                    cfg["colors"][k] = val

        for bool_k in ("low_quota_warn", "always_on_top"):
            if isinstance(raw.get(bool_k), bool):
                cfg[bool_k] = raw[bool_k]

        if raw.get("layout") in ("topbar", "columns", "horizontal", "vertical"):
            cfg["layout"] = raw["layout"]
        if raw.get("scale") in ("compact", "mini"):
            cfg["scale"] = raw["scale"]
        if raw.get("time_format") in ("relative", "abs_hm", "abs_smart"):
            cfg["time_format"] = raw["time_format"]
        if isinstance(raw.get("pill_opacity"), (int, float)):
            cfg["pill_opacity"] = max(0.15, min(1.0, float(raw["pill_opacity"])))
        if isinstance(raw.get("tip_opacity"), (int, float)):
            cfg["tip_opacity"] = max(0.20, min(1.0, float(raw["tip_opacity"])))
        if isinstance(raw.get("interval"), (int, float)):
            cfg["interval"] = max(15, min(3600, int(raw["interval"])))
        if isinstance(raw.get("timeout"), (int, float)):
            cfg["timeout"] = max(5, min(60, int(raw["timeout"])))
        if isinstance(raw.get("pos_x"), int) and isinstance(raw.get("pos_y"), int):
            cfg["pos_x"] = raw["pos_x"]
            cfg["pos_y"] = raw["pos_y"]
    except Exception as e:
        log_msg("WARN", f"Config load failed, using defaults: {e}")
    return cfg


def atomic_save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".cfg_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        log_msg("ERROR", f"Atomic config save failed: {e}")


def hex_to_rgb(hex_str):
    h = hex_str.lstrip('#')
    if len(h) != 6:
        return (0.05, 0.88, 0.52)
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def strip_ansi(text):
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)


def make_empty_slot(short_label="5h", wk_label="周"):
    return {
        "p1": None,
        "p1_label": short_label,
        "p1_rst": "--",
        "p2": None,
        "p2_label": wk_label,
        "p2_rst": "--",
        "pace": None,
        "extras": [],
        "credits": None,
        "exp": None,
        "ok": False,
        "stale": False,
        "err": None,
        "ts": None
    }


def infer_period_label(title_or_window, default_label="5h"):
    s = (title_or_window or "").lower()
    if "30-day" in s or "30d" in s or "month" in s:
        return "30d"
    if "week" in s or "7d" in s or "7-day" in s:
        return "周"
    if "day" in s or "24h" in s or "daily" in s:
        return "日"
    m = re.search(r"(\d+)\s*-\s*hour|(\d+)\s*h", s)
    if m:
        hrs = m.group(1) or m.group(2)
        return f"{hrs}h"
    return default_label


def format_reset_time(rst_str, mode="relative", base_ts=None):
    """Format an upstream countdown or RFC3339 timestamp in local time."""
    if not rst_str or rst_str == "--":
        return "--"
    s = str(rst_str).strip()
    target_dt = None
    if "T" in s and re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        try:
            target_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if target_dt.tzinfo:
                target_dt = target_dt.astimezone()
        except (ValueError, OverflowError):
            return s
    else:
        if mode == "relative":
            return s
        days = hours = mins = secs = 0
        matched = False
        for unit, pattern in [("d", r"(\d+)\s*d"), ("h", r"(\d+)\s*h"), ("m", r"(\d+)\s*m"), ("s", r"(\d+)\s*s")]:
            m = re.search(pattern, s.lower())
            if m:
                matched = True
                val = int(m.group(1))
                if unit == "d": days = val
                elif unit == "h": hours = val
                elif unit == "m": mins = val
                elif unit == "s": secs = val
        if not matched:
            return s
        base_dt = datetime.fromtimestamp(base_ts) if isinstance(base_ts, (int, float)) and base_ts > 0 else datetime.now()
        target_dt = base_dt + timedelta(days=days, hours=hours, minutes=mins, seconds=secs)

    if mode == "relative":
        seconds = max(0, int((target_dt - datetime.now(target_dt.tzinfo)).total_seconds()))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes = seconds // 60
        return (f"{days}d {hours}h" if days else f"{hours}h {minutes}m")
    if mode == "abs_hm":
        return target_dt.strftime("%H:%M")
    if mode == "abs_smart":
        now = datetime.now(target_dt.tzinfo)
        return target_dt.strftime("%H:%M" if target_dt.date() == now.date() else "%m-%d %H:%M")
    return s


def parse_pct_value(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not m:
        return None
    val = float(m.group(1))
    return max(0.0, min(100.0, val))


def parse_text_output(out, res):
    section = None
    last_slot = None

    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("== Codex"):
            section = "codex"
            res["codex"]["ok"] = True
            last_slot = None
            continue
        elif line.startswith("== Antigravity"):
            section = "agy"
            last_slot = None
            continue
        elif line.startswith("=="):
            section = None
            last_slot = None
            continue

        if "for muse" in line.lower() or section is None:
            continue

        if section == "codex":
            if line.lower().startswith("pace:"):
                res["codex"]["pace"] = line.split(":", 1)[1].strip()
                continue
            if line.lower().startswith("error") or "could not read codex" in line.lower():
                res["codex"]["err"] = line
                res["codex"]["ok"] = False
                continue
            if ":" in line and "%" in line:
                head, _ = line.split(":", 1)
                pct = parse_pct_value(line)
                if pct is not None:
                    hl = head.lower()
                    if "week" in hl:
                        res["codex"]["p2"] = pct
                        res["codex"]["p2_label"] = infer_period_label(head, "周")
                        last_slot = ("codex", "p2", None)
                    elif "session" in hl or "hour" in hl or "5h" in hl or "day" in hl:
                        res["codex"]["p1"] = pct
                        res["codex"]["p1_label"] = infer_period_label(head, "5h")
                        last_slot = ("codex", "p1", None)
                    else:
                        res["codex"]["extras"].append(f"{head}: {pct:g}%")
                        last_slot = ("codex", "extra", len(res["codex"]["extras"]) - 1)
            elif line.startswith("Resets ") and last_slot and last_slot[0] == "codex":
                rst_val = line.replace("Resets in ", "", 1).strip() if line.startswith("Resets in ") else line.strip()
                if last_slot[1] in ("p1", "p2"):
                    res["codex"][f"{last_slot[1]}_rst"] = rst_val
                elif last_slot[1] == "extra" and last_slot[2] is not None:
                    res["codex"]["extras"][last_slot[2]] += f" ({rst_val})"
                last_slot = None
            elif line.startswith("Limit Reset Credits:"):
                res["codex"]["credits"] = line.split(":", 1)[1].strip()
            elif line.startswith("Next reset credit expires in"):
                res["codex"]["exp"] = line.replace("Next reset credit expires in", "").strip()

        elif section == "agy":
            if line.lower().startswith("pace:"):
                continue
            if line.lower().startswith("error:") and "antigravity" in line.lower():
                res["agy_g"]["err"] = line
                res["agy_c"]["err"] = line
                continue
            if ":" in line and "%" in line:
                head, _ = line.split(":", 1)
                pct = parse_pct_value(line)
                if pct is not None:
                    hl = head.lower()
                    target = "agy_c" if ("claude" in hl or "gpt" in hl) else "agy_g"
                    res[target]["ok"] = True
                    is_weekly = "week" in hl or "7d" in hl
                    slot_key = "p2" if is_weekly else "p1"
                    default_lbl = "周" if is_weekly else "5h"

                    if res[target][slot_key] is None:
                        res[target][slot_key] = pct
                        res[target][f"{slot_key}_label"] = infer_period_label(head, default_lbl)
                        last_slot = (target, slot_key, None)
                    else:
                        res[target]["extras"].append(f"{head}: {pct:g}%")
                        last_slot = (target, "extra", len(res[target]["extras"]) - 1)
            elif line.startswith("Resets ") and last_slot and last_slot[0] in ("agy_g", "agy_c"):
                t_key, s_type, e_idx = last_slot
                rst_val = line.replace("Resets in ", "", 1).strip() if line.startswith("Resets in ") else line.strip()
                if s_type in ("p1", "p2"):
                    res[t_key][f"{s_type}_rst"] = rst_val
                elif s_type == "extra" and e_idx is not None:
                    res[t_key]["extras"][e_idx] += f" ({rst_val})"
                last_slot = None


def _parse_codex_json(stdout, res):
    """Read documented CodexBar usage JSON. usedPercent is *used*, not remaining."""
    doc = json.loads(stdout)
    entries = doc if isinstance(doc, list) else doc.get("providers", []) if isinstance(doc, dict) and "providers" in doc else [doc]
    if not isinstance(entries, list):
        entries = [entries]
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("provider") != "codex":
            continue
        usage = entry.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        slot = res["codex"]
        for field, target, default in (("primary", "p1", "5h"), ("secondary", "p2", "周")):
            window = usage.get(field)
            if not isinstance(window, dict):
                continue
            try:
                if isinstance(window.get("remainingPercent"), (int, float)):
                    pct = float(window["remainingPercent"])
                elif isinstance(window.get("usedPercent"), (int, float)):
                    pct = 100.0 - float(window["usedPercent"])
                else:
                    continue
                if not math.isfinite(pct):
                    continue
                slot[target] = max(0.0, min(100.0, pct))
            except (TypeError, ValueError, OverflowError):
                continue
            minutes = window.get("windowMinutes")
            if isinstance(minutes, (int, float)) and math.isfinite(minutes) and minutes > 0:
                if int(minutes) == 10080:
                    label = "周"
                elif int(minutes) % 1440 == 0:
                    label = f"{int(minutes) // 1440}d"
                elif int(minutes) % 60 == 0:
                    label = f"{int(minutes) // 60}h"
                else:
                    label = f"{int(minutes)}m"
                slot[f"{target}_label"] = label
            else:
                slot[f"{target}_label"] = default
            if isinstance(window.get("resetsAt"), str):
                slot[f"{target}_rst"] = window["resetsAt"]
        credits = usage.get("resetCredits") or entry.get("resetCredits") or {}
        if isinstance(credits, dict) and credits.get("available") is not None:
            slot["credits"] = f"{credits['available']} available"
            slot["exp"] = credits.get("nextExpiresAt") or "--"
        slot["ok"] = slot["p1"] is not None or slot["p2"] is not None
        return


def fetch_quotas(timeout_sec=15):
    """Fetch both sources without changing the user's CodexBar provider settings."""
    res = {
        "codex": make_empty_slot("5h", "周"),
        "agy_g": make_empty_slot("5h", "周"),
        "agy_c": make_empty_slot("5h", "周")
    }
    bin_path = os.environ.get("QUOTA_PILL_CODEXBAR") or shutil.which("codexbar")
    if not bin_path or not os.path.isfile(bin_path) or not os.access(bin_path, os.X_OK):
        for k in res:
            res[k]["err"] = "未找到 codexbar，请先安装官方 CLI"
        return res

    def run_provider(provider, fmt):
        args = [bin_path, "usage", "--provider", provider, "--format", fmt]
        if fmt == "text":
            args.append("--no-color")
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)

    def process_codex():
        try:
            cli = run_provider("codex", "json")
            if cli.returncode == 0 and cli.stdout.strip():
                _parse_codex_json(cli.stdout, res)
            if not res["codex"]["ok"]:
                # Older CLI versions may not support the JSON schema.
                cli = run_provider("codex", "text")
                parse_text_output(strip_ansi(cli.stdout or ""), res)
            if not res["codex"]["ok"]:
                res["codex"]["err"] = "Codex 额度不可用（检查登录状态或 CLI 版本）"
        except subprocess.TimeoutExpired:
            res["codex"]["err"] = f"Codex 刷新超时（{timeout_sec}s）"
        except Exception as exc:
            log_msg("WARN", f"Codex query failed: {type(exc).__name__}")
            res["codex"]["err"] = "Codex 数据解析失败"

    def process_antigravity():
        try:
            cli = run_provider("antigravity", "text")
            parse_text_output(strip_ansi(cli.stdout or ""), res)
            if not any(res[k]["ok"] for k in ("agy_g", "agy_c")):
                for k in ("agy_g", "agy_c"):
                    res[k]["err"] = "Antigravity 额度不可用（检查客户端与 CLI 输出）"
        except subprocess.TimeoutExpired:
            for k in ("agy_g", "agy_c"):
                res[k]["err"] = f"Antigravity 刷新超时（{timeout_sec}s）"
        except Exception as exc:
            log_msg("WARN", f"Antigravity query failed: {type(exc).__name__}")
            for k in ("agy_g", "agy_c"):
                res[k]["err"] = "Antigravity 数据解析失败"

    with ThreadPoolExecutor(max_workers=2) as pool:
        c = pool.submit(process_codex)
        a = pool.submit(process_antigravity)
        c.result()
        a.result()

    now_ts = time.time()
    for slot in res.values():
        if slot["ok"]:
            slot["ts"] = now_ts
    return res


class WindowPositioner:
    @staticmethod
    def get_monitors_rects():
        display = Gdk.Display.get_default()
        rects = []
        if not display:
            return [(0, 0, 1920, 1080)]
        n = display.get_n_monitors()
        for i in range(n):
            mon = display.get_monitor(i)
            if mon:
                g = mon.get_geometry()
                rects.append((g.x, g.y, g.width, g.height))
        return rects if rects else [(0, 0, 1920, 1080)]

    @staticmethod
    def get_primary_rect():
        display = Gdk.Display.get_default()
        if display:
            mon = display.get_primary_monitor() or display.get_monitor(0)
            if mon:
                g = mon.get_geometry()
                return (g.x, g.y, g.width, g.height)
        return (0, 0, 1920, 1080)

    @classmethod
    def default_position(cls, win_w, win_h):
        px, py, pw, ph = cls.get_primary_rect()
        return px + int(pw * 0.62), py + 2

    @classmethod
    def clamp_to_monitors(cls, x, y, win_w, win_h):
        rects = cls.get_monitors_rects()
        cx, cy = x + win_w // 2, y + win_h // 2
        for mx, my, mw, mh in rects:
            if mx <= cx <= mx + mw and my <= cy <= my + mh:
                nx = max(mx, min(x, mx + mw - win_w))
                ny = max(my, min(y, my + mh - win_h))
                return nx, ny
        best_mon = min(
            rects,
            key=lambda r: (cx - (r[0] + r[2] // 2)) ** 2 + (cy - (r[1] + r[3] // 2)) ** 2
        )
        mx, my, mw, mh = best_mon
        nx = max(mx, min(x, mx + mw - win_w))
        ny = max(my, min(y, my + mh - win_h))
        return nx, ny


class QuotaPill(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.cfg = validate_and_load_config()
        self.data = {
            "codex": make_empty_slot("5h", "周"),
            "agy_g": make_empty_slot("5h", "周"),
            "agy_c": make_empty_slot("5h", "周")
        }
        if os.path.exists(DATA_CACHE_PATH):
            try:
                with open(DATA_CACHE_PATH, "r", encoding="utf-8") as cf:
                    cached = json.load(cf)
                for k in ("codex", "agy_g", "agy_c"):
                    if k in cached and isinstance(cached[k], dict) and (cached[k].get("p1") is not None or cached[k].get("p2") is not None):
                        self.data[k].update(cached[k])
                        self.data[k]["stale"] = True
            except Exception:
                pass

        self.last_sync_time = None
        self.timer_id = None
        self.hover_timer_id = None
        self.save_pos_timer_id = None
        self.is_fetching = False
        self.hover_visible = False
        self.last_mouse_root = (0, 0)

        self.set_title("QuotaPill")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.apply_topmost()
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        screen.connect("monitors-changed", lambda *_: self.ensure_on_screen())
        screen.connect("size-changed", lambda *_: self.ensure_on_screen())

        self.darea = Gtk.DrawingArea()
        self.darea.connect("draw", self.on_draw)
        self.add(self.darea)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("button-press-event", self.on_button_press)
        self.connect("configure-event", self.on_configure)
        self.connect("enter-notify-event", self.on_mouse_enter)
        self.connect("motion-notify-event", self.on_mouse_motion)
        self.connect("leave-notify-event", self.on_mouse_leave)

        self.init_hover_card()
        self.recalc_size()
        self.restore_position()
        self.show_all()

        self.refresh_async()
        self.restart_timer()

    def apply_topmost(self):
        top = self.cfg.get("always_on_top", True)
        self.set_keep_above(top)
        if top:
            self.stick()
        else:
            self.unstick()

    def init_hover_card(self):
        self.tip_win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.tip_win.set_resizable(False)
        self.tip_win.set_app_paintable(True)
        screen = self.tip_win.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.tip_win.set_visual(visual)
        self.tip_win.connect("draw", self.on_draw_tip_bg)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(9)
        box.set_margin_bottom(9)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.tip_label = Gtk.Label()
        self.tip_label.set_xalign(0.0)
        self.tip_label.set_yalign(0.0)
        self.tip_label.set_line_wrap(False)
        box.pack_start(self.tip_label, False, False, 0)
        self.tip_win.add(box)

    def on_draw_tip_bg(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.draw_rounded_box(cr, 1, 1, w - 2, h - 2, 10)
        alpha = self.cfg.get("tip_opacity", 0.94)
        cr.set_source_rgba(0.09, 0.11, 0.10, alpha)
        cr.fill_preserve()
        cr.set_line_width(1.1)
        cr.set_source_rgba(1, 1, 1, min(0.30, alpha * 0.22))
        cr.stroke()
        return False

    def format_pct_detail(self, val):
        if val is None:
            return "--"
        if abs(val - round(val)) < 0.05:
            return f"{int(round(val))}%"
        return f"{val:.1f}%"

    def render_hover_markup(self):
        esc = GLib.markup_escape_text
        lines = ["<span foreground='white' size='9500'>"]
        t_mode = self.cfg.get("time_format", "relative")

        active = self.get_active_providers()
        for idx, pid in enumerate(active):
            d = self.data[pid]
            meta = PROVIDER_META[pid]
            color = esc(self.cfg["colors"].get(pid, "#0de085"))
            status_tag = ""
            if d.get("stale"):
                status_tag = " <span foreground='#f59e0b' size='small'>[缓存]</span>"
            elif not d.get("ok") and d.get("p1") is None:
                status_tag = " <span foreground='#ef4444' size='small'>[未就绪]</span>"

            lines.append(f"<b><span foreground='{color}'>● {esc(meta['name'])}</span></b>{status_tag}")
            p1_str = esc(self.format_pct_detail(d["p1"]))
            p2_str = esc(self.format_pct_detail(d["p2"]))
            l1 = esc(d["p1_label"])
            l2 = esc(d["p2_label"])
            base_ts = d.get("ts")
            r1 = esc(format_reset_time(d["p1_rst"], t_mode, base_ts))
            r2 = esc(format_reset_time(d["p2_rst"], t_mode, base_ts))

            lines.append(f"  {l1} 额度: <b>{p1_str}</b>  <span foreground='#b0b8b4'>(重置: {r1})</span>")
            if d["p2"] is not None or d["p2_rst"] != "--":
                lines.append(f"  {l2} 额度: <b>{p2_str}</b>  <span foreground='#b0b8b4'>(重置: {r2})</span>")

            if pid == "codex" and d.get("credits") is not None:
                cr_str = esc(str(d["credits"]))
                exp_str = esc(format_reset_time(d.get("exp") or "--", t_mode, base_ts))
                lines.append(f"  重置次数: <b>{cr_str}</b>  <span foreground='#b0b8b4'>(到期: {exp_str})</span>")

            if d.get("err"):
                lines.append(f"  <span size='small' foreground='#f87171'>⚠ {esc(str(d['err'])[:50])}</span>")

            if idx < len(active) - 1:
                lines.append("")

        sync_str = self.last_sync_time.strftime("%H:%M:%S") if self.last_sync_time else "离线缓存"
        lines.append(
            f"\n<span size='small' foreground='#808884'>"
            f"更新: {esc(sync_str)} (每{self.cfg['interval']}s) | 左键拖动 | 右键设置"
            f"</span></span>"
        )
        return "\n".join(lines)

    def position_hover_card_near_pill(self):
        self.tip_label.set_markup(self.render_hover_markup())
        self.tip_win.get_child().show_all()
        _, nat_req = self.tip_win.get_preferred_size()
        tw, th = max(180, nat_req.width), max(60, nat_req.height)
        self.tip_win.resize(tw, th)

        wx, wy = self.get_position()
        ww, wh = self.get_size()
        mx_root, my_root = self.last_mouse_root

        rects = WindowPositioner.get_monitors_rects()
        cx, cy = wx + ww // 2, wy + wh // 2
        mon_rect = rects[0]
        for r in rects:
            if r[0] <= cx <= r[0] + r[2] and r[1] <= cy <= r[1] + r[3]:
                mon_rect = r
                break
        mx, my, mw, mh = mon_rect

        layout = self.cfg.get("layout", "horizontal")
        if layout in ("vertical", "columns", "horizontal"):
            if wx - tw - 10 >= mx:
                tx = wx - tw - 8
            elif wx + ww + tw + 10 <= mx + mw:
                tx = wx + ww + 8
            else:
                tx = max(mx + 6, min(wx + (ww - tw) // 2, mx + mw - tw - 6))
            ty = my_root - th // 2 if my_root > 0 else wy
            ty = max(my + 6, min(ty, my + mh - th - 6))
        else:
            tx = max(mx + 6, min(wx + (ww - tw) // 2, mx + mw - tw - 6))
            if wy + wh + th + 10 <= my + mh:
                ty = wy + wh + 6
            else:
                ty = max(my + 6, wy - th - 6)

        self.tip_win.move(int(tx), int(ty))

    def show_hover_card(self):
        self.hover_timer_id = None
        self.hover_visible = True
        self.position_hover_card_near_pill()
        self.tip_win.show_all()
        self.tip_win.queue_draw()
        return False

    def refresh_hover_if_open(self):
        if self.hover_visible:
            self.position_hover_card_near_pill()
            self.tip_win.queue_draw()

    def on_mouse_enter(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.last_mouse_root = (int(event.x_root), int(event.y_root))
        if self.hover_timer_id:
            GLib.source_remove(self.hover_timer_id)
        self.hover_timer_id = GLib.timeout_add(60, self.show_hover_card)
        return False

    def on_mouse_motion(self, widget, event):
        self.last_mouse_root = (int(event.x_root), int(event.y_root))
        return False

    def on_mouse_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if self.hover_timer_id:
            GLib.source_remove(self.hover_timer_id)
            self.hover_timer_id = None
        self.hover_visible = False
        self.tip_win.hide()
        return False

    def get_active_providers(self):
        active = [k for k in ["agy_g", "agy_c", "codex"] if self.cfg["enabled"].get(k, False)]
        return active if active else ["agy_g"]

    def get_metrics(self):
        if self.cfg.get("scale") == "mini":
            return {"r": 10.0, "stroke": 2.4, "f_pct": 6.8, "f_sub": 5.2, "f_tag": 7.5, "cell_w": 24, "cell_h": 24}
        return {"r": 16.0, "stroke": 3.8, "f_pct": 9.2, "f_sub": 7.2, "f_tag": 8.5, "cell_w": 42, "cell_h": 42}

    def recalc_size(self):
        n = len(self.get_active_providers())
        m = self.get_metrics()
        cw, ch = m["cell_w"], m["cell_h"]
        layout = self.cfg.get("layout", "topbar")
        is_mini = self.cfg.get("scale") == "mini"

        if layout == "topbar":
            if is_mini:
                group_w = 18 + cw * 2 + 6
                w, h = 8 + n * group_w, 26
            else:
                group_w = cw * 2 + 12
                w, h = 10 + n * group_w, ch + 18
        elif layout == "columns":
            w, h = 10 + n * (cw + 8), ch * 2 + 24
        elif layout == "horizontal":
            w, h = cw * 2 + 20, 8 + n * (ch + 18)
        else:
            w, h = cw + 14, 8 + n * (ch * 2 + 18)

        self.darea.set_size_request(w, h)
        self.resize(w, h)
        self.darea.queue_draw()

    def restore_position(self):
        w, h = self.get_size()
        px, py = self.cfg.get("pos_x"), self.cfg.get("pos_y")
        if px is None or py is None:
            px, py = WindowPositioner.default_position(w, h)
        cx, cy = WindowPositioner.clamp_to_monitors(px, py, w, h)
        self.move(cx, cy)

    def ensure_on_screen(self):
        x, y = self.get_position()
        w, h = self.get_size()
        cx, cy = WindowPositioner.clamp_to_monitors(x, y, w, h)
        if (cx, cy) != (x, y):
            self.move(cx, cy)

    def on_configure(self, widget, event):
        x, y = self.get_position()
        if x != self.cfg.get("pos_x") or y != self.cfg.get("pos_y"):
            self.cfg["pos_x"], self.cfg["pos_y"] = x, y
            if self.save_pos_timer_id:
                GLib.source_remove(self.save_pos_timer_id)
            self.save_pos_timer_id = GLib.timeout_add(500, self._commit_pos_save)
        return False

    def _commit_pos_save(self):
        self.save_pos_timer_id = None
        atomic_save_config(self.cfg)
        return False

    def restart_timer(self):
        if self.timer_id:
            GLib.source_remove(self.timer_id)
        interval = max(15, int(self.cfg.get("interval", 60)))
        self.timer_id = GLib.timeout_add_seconds(interval, self.refresh_async)

    def refresh_async(self):
        if self.is_fetching:
            return True
        self.is_fetching = True
        timeout_sec = self.cfg.get("timeout", 15)

        def worker():
            fresh = fetch_quotas(timeout_sec=timeout_sec)
            GLib.idle_add(self.merge_and_update_ui, fresh)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def merge_and_update_ui(self, fresh):
        self.is_fetching = False
        any_ok = False
        for pid in ["codex", "agy_g", "agy_c"]:
            f_slot = fresh[pid]
            old_slot = self.data[pid]
            if f_slot["ok"] or f_slot["p1"] is not None:
                self.data[pid] = f_slot
                any_ok = True
            else:
                if old_slot["p1"] is not None or old_slot["p2"] is not None:
                    old_slot["stale"] = True
                    old_slot["err"] = f_slot.get("err")
                else:
                    self.data[pid] = f_slot

        if any_ok:
            self.last_sync_time = datetime.now()
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(DATA_CACHE_PATH, "w", encoding="utf-8") as cf:
                    json.dump(self.data, cf, ensure_ascii=False)
            except Exception:
                pass
        self.darea.queue_draw()
        self.refresh_hover_if_open()
        return False

    def on_button_press(self, widget, event):
        if event.button == 1:
            self.hover_visible = False
            self.tip_win.hide()
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        elif event.button == 3:
            self.hover_visible = False
            self.tip_win.hide()
            menu = self.build_menu()
            menu.popup_at_pointer(event)
            return True
        return False

    def build_menu(self):
        menu = Gtk.Menu()

        # 1. 选择显示的订阅
        sub_prov = Gtk.Menu()
        item_prov = Gtk.MenuItem(label="📊 选择显示订阅 (可多选)")
        item_prov.set_submenu(sub_prov)
        for pid, meta in PROVIDER_META.items():
            chk = Gtk.CheckMenuItem(label=meta["name"])
            chk.set_active(self.cfg["enabled"].get(pid, False))
            chk.connect("toggled", self.on_toggle_provider, pid)
            sub_prov.append(chk)
        menu.append(item_prov)

        # 2. 摆放方式
        sub_layout = Gtk.Menu()
        item_layout = Gtk.MenuItem(label="📐 摆放与排列方式")
        item_layout.set_submenu(sub_layout)
        layouts = [
            ("topbar", "一字横排 (配合26px版可嵌入顶栏)"),
            ("columns", "多列紧凑 (同订阅竖放 · 多订阅并排)"),
            ("horizontal", "同订阅横放 (5h/周左右排 · 多订阅上下叠)"),
            ("vertical", "单列竖条 (全竖向长胶囊)")
        ]
        group = None
        for l_key, l_label in layouts:
            rad = Gtk.RadioMenuItem.new_with_label(group, l_label)
            group = rad.get_group()
            rad.set_active(self.cfg.get("layout") == l_key)
            rad.connect("toggled", self.on_change_layout, l_key)
            sub_layout.append(rad)
        menu.append(item_layout)

        # 3. 尺寸大小
        sub_scale = Gtk.Menu()
        item_scale = Gtk.MenuItem(label="🔍 圆环尺寸大小")
        item_scale.set_submenu(sub_scale)
        s_group = None
        for s_key, s_label in [("compact", "紧凑版 (清晰环内文字)"), ("mini", "26px 顶栏嵌入版 (极窄高度)")]:
            s_rad = Gtk.RadioMenuItem.new_with_label(s_group, s_label)
            s_group = s_rad.get_group()
            s_rad.set_active(self.cfg.get("scale", "compact") == s_key)
            s_rad.connect("toggled", self.on_change_scale, s_key)
            sub_scale.append(s_rad)
        menu.append(item_scale)

        # 4. 胶囊与弹窗透明度调节
        sub_op = Gtk.Menu()
        item_op = Gtk.MenuItem(label="🌗 背景透明度调节")
        item_op.set_submenu(sub_op)

        pill_op_menu = Gtk.Menu()
        pill_op_item = Gtk.MenuItem(label=f"胶囊背景透明度 ({int(self.cfg.get('pill_opacity', 0.90)*100)}%)")
        pill_op_item.set_submenu(pill_op_menu)
        p_grp = None
        cur_p_op = round(self.cfg.get("pill_opacity", 0.90), 2)
        for val, lbl in OPACITY_PRESETS:
            rad = Gtk.RadioMenuItem.new_with_label(p_grp, lbl)
            p_grp = rad.get_group()
            rad.set_active(abs(cur_p_op - val) < 0.04)
            rad.connect("toggled", self.on_change_pill_opacity, val)
            pill_op_menu.append(rad)
        sub_op.append(pill_op_item)

        tip_op_menu = Gtk.Menu()
        tip_op_item = Gtk.MenuItem(label=f"悬浮窗背景透明度 ({int(self.cfg.get('tip_opacity', 0.94)*100)}%)")
        tip_op_item.set_submenu(tip_op_menu)
        t_grp = None
        cur_t_op = round(self.cfg.get("tip_opacity", 0.94), 2)
        for val, lbl in OPACITY_PRESETS:
            rad = Gtk.RadioMenuItem.new_with_label(t_grp, lbl)
            t_grp = rad.get_group()
            rad.set_active(abs(cur_t_op - val) < 0.04)
            rad.connect("toggled", self.on_change_tip_opacity, val)
            tip_op_menu.append(rad)
        sub_op.append(tip_op_item)

        menu.append(item_op)

        # 5. 颜色设置 (支持360°色轮选色 + 16色预设预览)
        sub_colors = Gtk.Menu()
        item_colors = Gtk.MenuItem(label="🎨 订阅圆环颜色配置")
        item_colors.set_submenu(sub_colors)
        for pid, meta in PROVIDER_META.items():
            p_menu = Gtk.Menu()
            cur_hex = self.cfg["colors"].get(pid, "#0de085")
            p_item = Gtk.MenuItem()
            p_lbl = Gtk.Label(xalign=0.0)
            p_lbl.set_markup(f"<span foreground='{cur_hex}'>●</span> {GLib.markup_escape_text(meta['name'])} ({cur_hex})")
            p_item.add(p_lbl)
            p_item.set_submenu(p_menu)

            wheel_item = Gtk.MenuItem(label="🎯 自定义拖动圆盘/色轮选色...")
            wheel_item.connect("activate", self.on_open_color_wheel, pid)
            p_menu.append(wheel_item)
            p_menu.append(Gtk.SeparatorMenuItem())

            for c_name, c_hex in PRESET_COLORS.items():
                c_item = Gtk.MenuItem()
                mark = "✓ " if cur_hex.lower() == c_hex.lower() else "    "
                lbl = Gtk.Label(xalign=0.0)
                lbl.set_markup(f"{mark}<span foreground='{c_hex}'>●</span> {GLib.markup_escape_text(c_name)} <span size='small' foreground='#888888'>({c_hex})</span>")
                c_item.add(lbl)
                c_item.connect("activate", self.on_select_preset_color, pid, c_hex)
                p_menu.append(c_item)
            sub_colors.append(p_item)

        sub_colors.append(Gtk.SeparatorMenuItem())
        chk_warn = Gtk.CheckMenuItem(label="低于 20% 或耗尽时强制变红预警")
        chk_warn.set_active(self.cfg.get("low_quota_warn", True))
        chk_warn.connect("toggled", self.on_toggle_warn)
        sub_colors.append(chk_warn)
        menu.append(item_colors)

        # 6. 重置时间显示格式 (相对倒计时 vs 绝对时间 HH:MM)
        sub_tf = Gtk.Menu()
        item_tf = Gtk.MenuItem(label="🕒 重置时间显示格式")
        item_tf.set_submenu(sub_tf)
        tf_group = None
        for tf_key, tf_label in [
            ("relative", "相对倒计时 (如 4h 30m)"),
            ("abs_hm", "绝对时间 - 仅时分 (如 02:30)"),
            ("abs_smart", "绝对时间 - 跨天含日期 (如 11-14 02:30)")
        ]:
            tf_rad = Gtk.RadioMenuItem.new_with_label(tf_group, tf_label)
            tf_group = tf_rad.get_group()
            tf_rad.set_active(self.cfg.get("time_format", "relative") == tf_key)
            tf_rad.connect("toggled", self.on_change_time_format, tf_key)
            sub_tf.append(tf_rad)
        menu.append(item_tf)

        # 7. 刷新间隔
        sub_int = Gtk.Menu()
        item_int = Gtk.MenuItem(label=f"⏱️ 刷新时间 ({self.cfg.get('interval', 60)}秒)")
        item_int.set_submenu(sub_int)
        i_group = None
        for sec, s_label in [(30, "30 秒"), (60, "1 分钟"), (120, "2 分钟"), (300, "5 分钟"), (600, "10 分钟")]:
            i_rad = Gtk.RadioMenuItem.new_with_label(i_group, s_label)
            i_group = i_rad.get_group()
            i_rad.set_active(self.cfg.get("interval", 60) == sec)
            i_rad.connect("toggled", self.on_change_interval, sec)
            sub_int.append(i_rad)
        menu.append(item_int)

        # 8. 置顶开关
        chk_top = Gtk.CheckMenuItem(label="📌 始终置顶 (Always on Top)")
        chk_top.set_active(self.cfg.get("always_on_top", True))
        chk_top.connect("toggled", self.on_toggle_topmost)
        menu.append(chk_top)

        menu.append(Gtk.SeparatorMenuItem())
        item_ref = Gtk.MenuItem(label="🔄 立即刷新额度")
        item_ref.connect("activate", lambda _: self.refresh_async())
        menu.append(item_ref)

        item_quit = Gtk.MenuItem(label="❌ 退出胶囊挂件")
        item_quit.connect("activate", Gtk.main_quit)
        menu.append(item_quit)

        menu.show_all()
        return menu

    def on_toggle_provider(self, widget, pid):
        self.cfg["enabled"][pid] = widget.get_active()
        if not any(self.cfg["enabled"].values()):
            self.cfg["enabled"][pid] = True
            widget.set_active(True)
        atomic_save_config(self.cfg)
        self.recalc_size()
        self.refresh_hover_if_open()

    def on_change_layout(self, widget, layout_key):
        if widget.get_active():
            self.cfg["layout"] = layout_key
            atomic_save_config(self.cfg)
            self.recalc_size()

    def on_change_scale(self, widget, scale_key):
        if widget.get_active():
            self.cfg["scale"] = scale_key
            atomic_save_config(self.cfg)
            self.recalc_size()

    def on_change_pill_opacity(self, widget, val):
        if widget.get_active():
            self.cfg["pill_opacity"] = val
            atomic_save_config(self.cfg)
            self.darea.queue_draw()

    def on_change_tip_opacity(self, widget, val):
        if widget.get_active():
            self.cfg["tip_opacity"] = val
            atomic_save_config(self.cfg)
            self.tip_win.queue_draw()

    def on_select_preset_color(self, widget, pid, c_hex):
        self.cfg["colors"][pid] = c_hex
        atomic_save_config(self.cfg)
        self.darea.queue_draw()
        self.refresh_hover_if_open()

    def on_open_color_wheel(self, widget, pid):
        meta = PROVIDER_META[pid]
        dlg = Gtk.ColorChooserDialog(title=f"选择圆环颜色 - {meta['name']}", transient_for=self)
        dlg.set_keep_above(True)
        dlg.set_use_alpha(False)
        dlg.set_property("show-editor", True)

        rgba = Gdk.RGBA()
        if not rgba.parse(self.cfg["colors"].get(pid, "#0de085")):
            rgba.parse("#0de085")
        dlg.set_rgba(rgba)

        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            c = dlg.get_rgba()
            r = int(round(max(0.0, min(1.0, c.red)) * 255))
            g = int(round(max(0.0, min(1.0, c.green)) * 255))
            b = int(round(max(0.0, min(1.0, c.blue)) * 255))
            hex_val = f"#{r:02x}{g:02x}{b:02x}"
            self.cfg["colors"][pid] = hex_val
            atomic_save_config(self.cfg)
            self.darea.queue_draw()
            self.refresh_hover_if_open()
        dlg.destroy()

    def on_toggle_warn(self, widget):
        self.cfg["low_quota_warn"] = widget.get_active()
        atomic_save_config(self.cfg)
        self.darea.queue_draw()

    def on_change_time_format(self, widget, tf_key):
        if widget.get_active():
            self.cfg["time_format"] = tf_key
            atomic_save_config(self.cfg)
            self.refresh_hover_if_open()

    def on_change_interval(self, widget, sec):
        if widget.get_active():
            self.cfg["interval"] = sec
            atomic_save_config(self.cfg)
            self.restart_timer()
            self.refresh_hover_if_open()

    def on_toggle_topmost(self, widget):
        self.cfg["always_on_top"] = widget.get_active()
        atomic_save_config(self.cfg)
        self.apply_topmost()

    def draw_rounded_box(self, cr, x, y, bw, bh, radius):
        r = min(radius, bw / 2.0, bh / 2.0)
        cr.new_sub_path()
        cr.arc(x + bw - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + bw - r, y + bh - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + bh - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def draw_centered_text(self, cr, cx, y, text, size, bold=False, alpha=0.95, rgb=(1, 1, 1)):
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2 - ext.x_bearing, y)
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        cr.show_text(text)
        cr.new_path()

    def draw_ring_unit(self, cr, cx, cy, m, pct, short_label, hex_color):
        r = m["r"]
        warn_enabled = self.cfg.get("low_quota_warn", True)
        is_exhausted = (pct is not None and pct <= 0.0)
        is_low = (pct is not None and pct < 20.0)

        cr.new_sub_path()
        cr.set_line_width(m["stroke"])
        if warn_enabled and is_exhausted:
            cr.set_source_rgba(0.95, 0.22, 0.22, 0.45)
        else:
            cr.set_source_rgba(1, 1, 1, 0.13)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()

        if pct is not None and pct > 0:
            cr.new_sub_path()
            if warn_enabled and is_low:
                cr.set_source_rgba(1.0, 0.30, 0.30, 0.98)
            else:
                red, green, blue = hex_to_rgb(hex_color)
                cr.set_source_rgba(red, green, blue, 0.96)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            start_angle = -math.pi / 2
            end_angle = start_angle + (2 * math.pi * (max(0.0, min(100.0, pct)) / 100.0))
            cr.arc(cx, cy, r, start_angle, end_angle)
            cr.stroke()

        if pct is None:
            pct_str = "--"
            txt_rgb = (0.65, 0.65, 0.65)
        else:
            pct_str = f"{int(round(pct))}%"
            txt_rgb = (1.0, 0.35, 0.35) if (warn_enabled and is_low) else (1.0, 1.0, 1.0)

        self.draw_centered_text(cr, cx, cy - 0.5, pct_str, m["f_pct"], bold=True, alpha=0.98, rgb=txt_rgb)
        self.draw_centered_text(cr, cx, cy + m["f_sub"] + 0.5, short_label, m["f_sub"], bold=False, alpha=0.68)

    def on_draw(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        active = self.get_active_providers()
        layout = self.cfg.get("layout", "topbar")
        is_mini = self.cfg.get("scale") == "mini"
        m = self.get_metrics()
        cw, ch = m["cell_w"], m["cell_h"]
        bg_alpha = self.cfg.get("pill_opacity", 0.90)

        self.draw_rounded_box(cr, 1, 1, w - 2, h - 2, min(w, h) / 2.0 if (len(active) == 1 or is_mini) else 15)
        cr.set_source_rgba(0.10, 0.12, 0.11, bg_alpha)
        cr.fill_preserve()
        cr.set_line_width(1.1)
        cr.set_source_rgba(1, 1, 1, min(0.28, bg_alpha * 0.18))
        cr.stroke()

        for idx, pid in enumerate(active):
            p_data = self.data.get(pid, make_empty_slot())
            p1, p2 = p_data["p1"], p_data["p2"]
            l1, l2 = p_data["p1_label"], p_data["p2_label"]
            c_hex = self.cfg["colors"].get(pid, "#0de085")
            short_tag = PROVIDER_META[pid]["short"]
            badge = PROVIDER_META[pid]["badge"]
            tag_rgb = hex_to_rgb(c_hex)

            if layout == "topbar":
                if is_mini:
                    group_w = 18 + cw * 2 + 6
                    gx = 4 + idx * group_w
                    if idx > 0:
                        cr.new_path()
                        cr.set_line_width(1.0)
                        cr.set_source_rgba(1, 1, 1, 0.14)
                        cr.move_to(gx, 5)
                        cr.line_to(gx, h - 5)
                        cr.stroke()
                    cy = h / 2.0
                    self.draw_centered_text(cr, gx + 10, cy + 3, badge, m["f_tag"], bold=True, alpha=0.95, rgb=tag_rgb)
                    self.draw_ring_unit(cr, gx + 18 + cw * 0.5, cy, m, p1, l1, c_hex)
                    self.draw_ring_unit(cr, gx + 20 + cw * 1.5, cy, m, p2, l2, c_hex)
                else:
                    group_w = cw * 2 + 12
                    gx = 5 + idx * group_w
                    if idx > 0:
                        cr.new_path()
                        cr.set_line_width(1.0)
                        cr.set_source_rgba(1, 1, 1, 0.12)
                        cr.move_to(gx, 10)
                        cr.line_to(gx, h - 10)
                        cr.stroke()
                    self.draw_centered_text(cr, gx + group_w / 2.0, 12, short_tag, m["f_tag"], bold=True, alpha=0.90, rgb=tag_rgb)
                    cy = 14 + ch / 2.0
                    self.draw_ring_unit(cr, gx + 6 + cw * 0.5, cy, m, p1, l1, c_hex)
                    self.draw_ring_unit(cr, gx + 6 + cw * 1.5, cy, m, p2, l2, c_hex)

            elif layout == "columns":
                col_w = cw + 8
                gx = 5 + idx * col_w
                cx = gx + col_w / 2.0
                if idx > 0:
                    cr.new_path()
                    cr.set_line_width(1.0)
                    cr.set_source_rgba(1, 1, 1, 0.12)
                    cr.move_to(gx, 12)
                    cr.line_to(gx, h - 12)
                    cr.stroke()
                self.draw_centered_text(cr, cx, 14, short_tag, m["f_tag"], bold=True, alpha=0.90, rgb=tag_rgb)
                self.draw_ring_unit(cr, cx, 18 + ch * 0.5, m, p1, l1, c_hex)
                self.draw_ring_unit(cr, cx, 20 + ch * 1.5, m, p2, l2, c_hex)

            elif layout == "horizontal":
                row_h = ch + 18
                gy = 4 + idx * row_h
                if idx > 0:
                    cr.new_path()
                    cr.set_line_width(1.0)
                    cr.set_source_rgba(1, 1, 1, 0.12)
                    cr.move_to(12, gy)
                    cr.line_to(w - 12, gy)
                    cr.stroke()
                self.draw_centered_text(cr, w / 2.0, gy + 12, short_tag, m["f_tag"], bold=True, alpha=0.90, rgb=tag_rgb)
                cy = gy + 14 + ch / 2.0
                self.draw_ring_unit(cr, w * 0.28, cy, m, p1, l1, c_hex)
                self.draw_ring_unit(cr, w * 0.72, cy, m, p2, l2, c_hex)

            else:
                sec_h = ch * 2 + 18
                gy = 4 + idx * sec_h
                cx = w / 2.0
                if idx > 0:
                    cr.new_path()
                    cr.set_line_width(1.0)
                    cr.set_source_rgba(1, 1, 1, 0.12)
                    cr.move_to(10, gy)
                    cr.line_to(w - 10, gy)
                    cr.stroke()
                self.draw_centered_text(cr, cx, gy + 12, short_tag, m["f_tag"], bold=True, alpha=0.90, rgb=tag_rgb)
                self.draw_ring_unit(cr, cx, gy + 14 + ch * 0.5, m, p1, l1, c_hex)
                self.draw_ring_unit(cr, cx, gy + 16 + ch * 1.5, m, p2, l2, c_hex)


def acquire_single_instance():
    os.makedirs(CACHE_DIR, exist_ok=True)
    lock_fd = open(PID_PATH, "a+")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log_msg("INFO", "Another QuotaPill instance is already running")
        sys.exit(0)
    lock_fd.seek(0)
    lock_fd.truncate()
    lock_fd.write(str(os.getpid()))
    lock_fd.flush()
    return lock_fd


if __name__ == "__main__":
    _lock = acquire_single_instance()
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    QuotaPill()
    Gtk.main()
