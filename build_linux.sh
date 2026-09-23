#!/usr/bin/env bash
# Local Ubuntu x86_64 entry. Inputs: build.py arguments plus ../build resources.
# Delegates all building/validation to build.py; never installs or cleans by default.
set -euo pipefail

easyqc_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
easyqc_build_root="$easyqc_source_dir/../build/linux-x86_64"
easyqc_build_python="$easyqc_build_root/venv/bin/python"
easyqc_cursor_deb="$easyqc_build_root/deps/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb"

if [[ ! -x "$easyqc_build_python" ]]; then
    printf '缺少打包环境：%s\n请参阅 docs/guide/12-native-installers.md。\n' "$easyqc_build_python" >&2
    exit 1
fi
if [[ ! -f "$easyqc_cursor_deb" ]]; then
    printf '缺少 Linux 打包依赖：%s\n请参阅 docs/guide/12-native-installers.md。\n' "$easyqc_cursor_deb" >&2
    exit 1
fi

export MPLCONFIGDIR="$easyqc_build_root/mplconfig"
cd -- "$easyqc_source_dir"
exec "$easyqc_build_python" "$easyqc_source_dir/build.py" \
    --linux-cursor-deb "$easyqc_cursor_deb" "$@"
