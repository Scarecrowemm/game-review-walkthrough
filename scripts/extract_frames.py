#!/usr/bin/env python3
"""从游戏录像里抽帧，作为攻略配图与 E1/E2 级证据。

只读取视频文件，不修改原文件。抽出的帧是真实游戏画面，属于
`kind=user_capture`，可以直接挂到任意攻略条目上。

用法:
    # 按时间戳抽
    python extract_frames.py boss2.mp4 --at 00:32,02:14,03:05 -o out/frames

    # 指定区间内均匀抽 4 帧
    python extract_frames.py ch3.mp4 --range 12:30-14:00 --count 4 -o out/frames

依赖: ffmpeg（免费，未安装时脚本会给出安装指引，不会假装成功）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 常见安装位置，用于 ffmpeg 不在 PATH 里的情况
CANDIDATE_DIRS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
    os.path.expandvars(r"%USERPROFILE%\scoop\shims"),
    r"C:\ProgramData\chocolatey\bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
]

INSTALL_HINT = """\
未找到 ffmpeg。抽帧需要它，它本身是免费开源工具，装一次长期可用：

  Windows (winget)   winget install --id Gyan.FFmpeg -e
  Windows (scoop)    scoop install ffmpeg
  Windows (choco)    choco install ffmpeg
  macOS              brew install ffmpeg
  其他平台            https://ffmpeg.org/download.html

装完后重开一个终端让 PATH 生效，再运行本脚本。

已经把 ffmpeg.exe 放在别处的话，直接指路即可，不用改系统 PATH
（Git Bash 下路径写法）:
  export GAME_REVIEW_FFMPEG="/c/tools/ffmpeg/bin/ffmpeg.exe"
"""


def find_ffmpeg() -> str | None:
    override = os.environ.get("GAME_REVIEW_FFMPEG")
    if override:
        p = Path(override).expanduser()
        return str(p) if p.is_file() else None

    found = shutil.which("ffmpeg")
    if found:
        return found

    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for d in CANDIDATE_DIRS:
        if not d:
            continue
        cand = Path(d) / exe
        if cand.is_file():
            return str(cand)
    return None


TS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?$")


def parse_ts(text: str) -> float:
    """接受 HH:MM:SS(.ms) / MM:SS(.ms) / 纯秒数。"""
    raw = text.strip()
    if not raw:
        raise ValueError("空时间戳")
    if raw.replace(".", "", 1).isdigit() and ":" not in raw:
        return float(raw)
    m = TS_RE.match(raw)
    if not m:
        raise ValueError(f"无法解析时间戳: {text!r}（用 HH:MM:SS 或 MM:SS）")
    hours = int(m.group(1) or 0)
    return hours * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float("0" + (m.group(4) and "." + m.group(4) or ""))


def ts_label(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}{total % 3600 // 60:02d}{total % 60:02d}"


def ts_readable(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def parse_range(text: str) -> tuple[float, float]:
    if "-" not in text:
        raise ValueError("--range 需要写成 开始-结束，例如 12:30-14:00")
    a, b = text.split("-", 1)
    start, end = parse_ts(a), parse_ts(b)
    if end <= start:
        raise ValueError("--range 的结束时间必须晚于开始时间")
    return start, end


def build_timestamps(args) -> list[float]:
    if args.at:
        return [parse_ts(x) for x in str(args.at).split(",") if x.strip()]
    if args.range:
        start, end = parse_range(args.range)
        count = max(1, int(args.count or 1))
        if count == 1:
            return [(start + end) / 2]
        step = (end - start) / (count - 1)
        return [start + step * i for i in range(count)]
    raise SystemExit("请指定 --at 或 --range。用 -h 看用法。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从游戏录像抽帧，作为攻略配图与 E1/E2 证据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python extract_frames.py boss2.mp4 --at 00:32,02:14 -o out/frames\n"
               "  python extract_frames.py ch3.mp4 --range 12:30-14:00 --count 4 -o out/frames\n",
    )
    parser.add_argument("video", help="录像文件路径")
    parser.add_argument("--at", help="时间戳，逗号分隔，如 00:32,02:14")
    parser.add_argument("--range", dest="range_", help="区间内均匀抽帧，如 12:30-14:00 配合 --count")
    parser.add_argument("--count", type=int, default=4, help="--range 下抽取的帧数，默认 4")
    parser.add_argument("-o", "--out", default="frames", help="输出目录，默认 frames")
    parser.add_argument("--prefix", default=None, help="文件名前缀，默认取视频文件名")
    parser.add_argument("--quality", type=int, default=3, help="jpg 质量 2-31，数字越小越清晰，默认 3")
    parser.add_argument("--width", type=int, default=1280, help="输出宽度，默认 1280（高度按比例）")
    parser.add_argument("--json", action="store_true", help="直接输出可粘贴进 report-data.json 的 images 片段")
    args = parser.parse_args()

    video = Path(args.video).expanduser()
    if not video.is_file():
        print(f"[x] 找不到录像文件: {video}", file=sys.stderr)
        return 2

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print(INSTALL_HINT, file=sys.stderr)
        return 3

    try:
        stamps = build_timestamps(args)
    except (ValueError, SystemExit) as exc:
        if isinstance(exc, SystemExit):
            raise
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = args.prefix or re.sub(r"[^\w\-]+", "_", video.stem)
    written: list[dict] = []

    for idx, t in enumerate(stamps, start=1):
        name = f"{prefix}_{ts_label(t)}.jpg"
        dest = out_dir / name
        cmd = [
            ffmpeg, "-nostdin", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale={args.width}:-2",
            "-q:v", str(args.quality),
            str(dest),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not dest.is_file():
            print(f"[!] {ts_readable(t)} 抽帧失败：{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else '未知错误'}", file=sys.stderr)
            continue
        size_kb = dest.stat().st_size / 1024
        written.append({"path": str(dest), "timestamp": ts_readable(t), "size_kb": round(size_kb, 1)})
        print(f"[v] {ts_readable(t)} -> {dest}  ({size_kb:.0f} KB)")

    if not written:
        print("[x] 一帧都没抽出来。检查时间戳是否超出视频长度。", file=sys.stderr)
        return 1

    print(f"\n[i] 共 {len(written)} 帧。这些是真实游戏画面，kind 填 user_capture。")
    print("[i] caption 必须说明「这张图让玩家看什么」，不要写「游戏截图」这种废话。\n")

    snippet = {
        "images": [
            {
                "kind": "user_capture",
                "path": f["path"],
                "timestamp": f["timestamp"],
                "caption": "【待填：这张图让玩家看什么】",
                "anchors": ["【待填：图中要点，如箭头指向的机关位置】"],
            }
            for f in written
        ]
    }
    print(json.dumps(snippet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # stdout 被重定向到管道时，Windows 用的是 ANSI 代码页（英文系统为 cp1252），
    # print 中文会直接 UnicodeEncodeError 崩掉——CI 的 windows runner 必然踩中。
    # 本机是中文 Windows 或 Git Bash（UTF-8），永远看不到这个问题。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
