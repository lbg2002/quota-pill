#!/usr/bin/env bash
# QuotaPill safe user-local installer for Ubuntu / Debian.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CLI_DIR="$HOME/.local/lib/codexbar-cli"
CACHE_DIR="$HOME/.cache/quota-pill"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/quota-pill.desktop"
START=1
AUTOSTART=1
SKIP_DEPS=0

for arg in "$@"; do
    case "$arg" in
        --no-start) START=0 ;;
        --no-autostart) AUTOSTART=0 ;;
        --skip-deps) SKIP_DEPS=1 ;; # for offline packaging checks / preinstalled dependencies
        -h|--help)
            printf '%s\n' '用法: ./install.sh [--no-start] [--no-autostart] [--skip-deps]'
            exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

if [[ ! -r "$ROOT/quota_pill.py" ]]; then
    echo "未找到程序文件 quota_pill.py，请从完整源码目录运行。" >&2
    exit 1
fi

if (( SKIP_DEPS == 0 )); then
    echo '检查 GTK3 / Cairo / 下载工具依赖…'
    deps=(python3-gi python3-cairo gir1.2-gtk-3.0 curl tar ca-certificates)
    missing=()
    for pkg in "${deps[@]}"; do
        if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
            missing+=("$pkg")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        if ! command -v apt-get >/dev/null 2>&1; then
            echo "缺少依赖: ${missing[*]}；本安装程序仅支持 Debian/Ubuntu APT。" >&2
            exit 1
        fi
        echo "将使用 APT 安装: ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    fi
    /usr/bin/python3 -c 'import gi, cairo; gi.require_version("Gtk", "3.0")' || {
        echo 'GTK3 / Cairo Python 依赖检测失败' >&2; exit 1;
    }
fi

mkdir -p "$BIN_DIR" "$CACHE_DIR" "$AUTOSTART_DIR"

install_official_cli() (
    local arch tag filename archive checksum tmp
    arch="$(uname -m)"
    case "$arch" in
        x86_64|aarch64) ;;
        *) echo "CodexBar 不支持此架构: $arch" >&2; exit 1 ;;
    esac
    command -v curl >/dev/null || { echo '缺少 curl，请先安装。' >&2; exit 1; }
    command -v tar >/dev/null || { echo '缺少 tar，请先安装。' >&2; exit 1; }
    tmp="$(mktemp -d)"
    trap 'rm -rf -- "$tmp"' EXIT
    curl -fsSL --retry 2 'https://api.github.com/repos/steipete/CodexBar/releases/latest' -o "$tmp/release.json"
    tag="$(python3 - "$tmp/release.json" <<'PY'
import json,sys,re
v=json.load(open(sys.argv[1],encoding='utf-8'))['tag_name']
if not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.]+)?',v):
    raise SystemExit('Unexpected release tag')
print(v)
PY
)"
    filename="CodexBarCLI-${tag}-linux-${arch}.tar.gz"
    # Exact filename prevents accidentally selecting the musl build.
    if ! python3 - "$tmp/release.json" "$filename" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1]))
assets={a.get('name','') for a in obj.get('assets', [])}
name=sys.argv[2]
if name not in assets or f'{name}.sha256' not in assets:
    raise SystemExit('Official glibc CLI or checksum asset missing')
PY
    then
        echo '未找到官方 Linux CLI 资源。请手动安装 CodexBar CLI 后重试。' >&2
        exit 1
    fi
    archive="$tmp/$filename"
    checksum="$tmp/$filename.sha256"
    local base="https://github.com/steipete/CodexBar/releases/download/$tag"
    echo "下载官方 CodexBar CLI: $tag ($arch)…"
    curl -fLsS --retry 2 "$base/$filename" -o "$archive"
    curl -fLsS --retry 2 "$base/$filename.sha256" -o "$checksum"
    (cd "$tmp" && sha256sum -c "${filename}.sha256")
    mkdir -p "$CLI_DIR"
    tar -xzf "$archive" -C "$CLI_DIR"
    if [[ ! -x "$CLI_DIR/codexbar" && ! -x "$CLI_DIR/CodexBarCLI" ]]; then
        echo '官方压缩包未包含预期的 CLI 可执行文件。' >&2
        exit 1
    fi
    if [[ ! -e "$CLI_DIR/codexbar" ]]; then
        ln -s CodexBarCLI "$CLI_DIR/codexbar"
    fi
    if [[ -e "$BIN_DIR/codexbar" || -L "$BIN_DIR/codexbar" ]]; then
        echo '已有 ~/.local/bin/codexbar，拒绝覆盖现有文件。' >&2
        exit 1
    fi
    ln -s "$CLI_DIR/codexbar" "$BIN_DIR/codexbar"
    "$BIN_DIR/codexbar" --version
)

if command -v codexbar >/dev/null 2>&1; then
    echo "使用现有 CodexBar CLI: $(command -v codexbar)"
elif [[ -x "$BIN_DIR/codexbar" ]]; then
    echo "使用现有 CodexBar CLI: $BIN_DIR/codexbar"
else
    echo '未发现 CodexBar CLI，安装官方发行包（不使用第三方脚本）…'
    install_official_cli
fi

# Only install QuotaPill's own files. Do not change ~/.codexbar/config.json.
if [[ -e "$BIN_DIR/quota-pill" && ! -f "$BIN_DIR/quota-pill" ]]; then
    echo "拒绝覆盖非普通文件: $BIN_DIR/quota-pill" >&2
    exit 1
fi
install -m 0755 "$ROOT/quota_pill.py" "$BIN_DIR/quota-pill"
echo "已安装: $BIN_DIR/quota-pill"

if (( AUTOSTART )); then
    # Desktop Entry Exec requires quoting an executable path with spaces.
    python3 - "$BIN_DIR/quota-pill" "$AUTOSTART_FILE" <<'PY'
import sys
from pathlib import Path
binary=sys.argv[1]
# Escape freedesktop Exec quoted-string metacharacters.
escaped=binary.replace('\\', '\\\\').replace('"','\\"').replace('$','\\$').replace('`','\\`').replace('%','%%')
content=f'''[Desktop Entry]\nType=Application\nName=QuotaPill\nComment=Antigravity and Codex quota ring widget\nExec="{escaped}"\nTerminal=false\nNoDisplay=false\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\n'''
Path(sys.argv[2]).write_text(content, encoding='utf-8')
PY
    echo '已启用登录自启动。'
else
    if [[ -f "$AUTOSTART_FILE" ]]; then
        rm -- "$AUTOSTART_FILE"
    fi
    echo '未启用开机自启（--no-autostart）。'
fi

if (( START )); then
    if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        pid_file="$CACHE_DIR/quota-pill.pid"
        if [[ -f "$pid_file" ]]; then
            old_pid="$(cat "$pid_file" 2>/dev/null || true)"
            if [[ "$old_pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$old_pid/cmdline" ]]; then
                # Only stop a running process if its command line matches our installed script.
                if tr '\0' '\n' < "/proc/$old_pid/cmdline" | grep -Fxq "$BIN_DIR/quota-pill"; then
                    kill -TERM "$old_pid" 2>/dev/null || true
                    sleep 0.5
                fi
            fi
        fi
        nohup "$BIN_DIR/quota-pill" >> "$CACHE_DIR/startup.log" 2>&1 </dev/null &
        echo 'QuotaPill 已尝试启动。日志: ~/.cache/quota-pill/startup.log'
    else
        echo '当前无图形会话，已安装；登录桌面后运行 quota-pill。'
    fi
fi

echo '安装完成。此脚本未改动已有 CodexBar 配置、认证信息或其他订阅。'
