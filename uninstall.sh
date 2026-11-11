#!/usr/bin/env bash
set -euo pipefail
bin="$HOME/.local/bin/quota-pill"
pid_file="$HOME/.cache/quota-pill/quota-pill.pid"
autostart="$HOME/.config/autostart/quota-pill.desktop"
if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$pid/cmdline" ]]; then
        if tr '\0' '\n' < "/proc/$pid/cmdline" | grep -Fxq "$bin"; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    fi
fi
[[ ! -f "$bin" ]] || rm -- "$bin"
[[ ! -f "$autostart" ]] || rm -- "$autostart"
echo '已移除 QuotaPill 程序与自启动项。'
echo '为避免数据丢失，保留了 ~/.config/quota-pill 与 ~/.cache/quota-pill。'
echo '不会卸载共享的 CodexBar CLI，也不会更改其配置或登录状态。'
