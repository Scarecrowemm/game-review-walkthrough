#!/usr/bin/env python3
"""把本技能导出到其他 agent 的技能目录（Codex / ZCode / WorkBuddy 用户级）。

先做可移植性预检（frontmatter 限制、字符上限、硬编码路径、脚本能否编译），
再复制。目标已存在时默认不覆盖，必须显式 --force，且覆盖前会先改名备份。

用法:
    python export_to_agent.py --target all --dry-run     # 只预检，不落盘
    python export_to_agent.py --target all               # 导出
    python export_to_agent.py --target codex --force     # 覆盖已有（先备份）
"""

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

# 各 agent 的用户级技能目录（相对于用户主目录）
TARGETS = {
    "codex": (".agents/skills", "Codex CLI / IDE 扩展（/skills 或 $技能名 调用）"),
    "zcode": (".zcode/skills", "ZCode（设置→技能，$技能名 调用）"),
    "workbuddy": (".workbuddy/skills", "WorkBuddy（会话自动加载）"),
}

# ZCode 的硬性限制；Codex 用同一个 SKILL.md 标准
DESC_MAX = 1024
BODY_MAX_KB = 100

EXCLUDE_DIRS = {"__pycache__", "out_test", "dist", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".bak"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}

# 只在 WorkBuddy 内部有意义的 frontmatter 字段，导出时剔除
HOST_ONLY_KEYS = {"agent_created"}


class Report:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.warn: list[str] = []
        self.fail: list[str] = []

    def add(self, level: str, msg: str) -> None:
        getattr(self, level).append(msg)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """返回 (frontmatter 键值, 正文)。无 frontmatter 时返回空字典。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2]


def preflight(rep: Report) -> str:
    """返回技能名。任何 fail 都代表导到别处会出问题。"""
    skill_md = SKILL_ROOT / "SKILL.md"
    if not skill_md.is_file():
        rep.add("fail", f"找不到 {skill_md}")
        return ""

    text = skill_md.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    name = meta.get("name", "").strip()
    desc = meta.get("description", "").strip()

    # 两个平台都要求 name + description，缺一个整个技能被忽略
    if not name:
        rep.add("fail", "frontmatter 缺 name —— Codex/ZCode 会直接忽略该技能")
    else:
        rep.add("ok", f"name = {name}")

    if not desc:
        rep.add("fail", "frontmatter 缺 description —— 隐式触发会失效")
    elif len(desc) > DESC_MAX:
        rep.add("fail", f"description {len(desc)} 字符 > {DESC_MAX} 上限，ZCode 会整条丢弃（非截断）")
    else:
        rep.add("ok", f"description {len(desc)} 字符（上限 {DESC_MAX}，余量 {DESC_MAX - len(desc)}）")

    body_kb = len(body.encode("utf-8")) / 1024
    if body_kb > BODY_MAX_KB:
        rep.add("fail", f"正文 {body_kb:.1f} KB > {BODY_MAX_KB} KB，ZCode 加载时会截断")
    else:
        rep.add("ok", f"正文 {body_kb:.1f} KB（上限 {BODY_MAX_KB} KB）")

    # 硬编码路径：换机器就断
    home_pat = re.compile(r"(C:\\\\?Users\\\\?[^\"'\s]+|/Users/[^\"'\s]+|/home/[^\"'\s]+)")
    hits: list[str] = []
    for p in SKILL_ROOT.rglob("*"):
        if not p.is_file() or p.name == Path(__file__).name:
            continue
        if p.suffix.lower() not in {".md", ".py", ".html", ".css", ".json"}:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if home_pat.search(line):
                    hits.append(f"{p.relative_to(SKILL_ROOT)}:{i}")
        except (UnicodeDecodeError, OSError):
            continue
    if hits:
        rep.add("fail", f"发现 {len(hits)} 处硬编码用户目录：{', '.join(hits[:3])}")
    else:
        rep.add("ok", "无硬编码用户目录，可跨机器复制")

    # 脚本能否编译（换机器最容易死在语法版本上）
    py_files = sorted((SKILL_ROOT / "scripts").glob("*.py"))
    with tempfile.TemporaryDirectory() as td:
        bad = []
        for f in py_files:
            try:
                py_compile.compile(str(f), cfile=str(Path(td) / (f.stem + ".pyc")), doraise=True)
            except py_compile.PyCompileError as e:
                bad.append(f"{f.name}: {str(e).splitlines()[0]}")
        if bad:
            rep.add("fail", f"脚本编译失败：{bad}")
        else:
            rep.add("ok", f"{len(py_files)} 个脚本编译通过（需 Python 3.9+，实测 3.10/3.13 均可）")

    # 外部依赖
    if shutil.which("ffmpeg"):
        rep.add("ok", "ffmpeg 已安装，抽帧功能可用")
    else:
        rep.add("warn", "未装 ffmpeg —— 抽帧不可用，其余功能不受影响（脚本会打印安装指引）")

    rep.add("ok", "仅用 Python 标准库，目标机器无需 pip install")
    return name


def should_copy(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if rel.name in EXCLUDE_NAMES or rel.suffix.lower() in EXCLUDE_SUFFIX:
        return False
    return True


def export(name: str, dest_root: Path, force: bool, dry: bool, rep: Report) -> dict:
    dest = dest_root / name
    result = {"target": str(dest), "status": "", "files": 0}

    # 从已导出的副本运行时，源和目标是同一个目录，不能自己导出到自己
    try:
        if dest.exists() and dest.resolve() == SKILL_ROOT.resolve():
            result["status"] = "源与目标相同，跳过"
            rep.add("warn", f"{dest} 就是当前源目录（你在从已导出的副本运行），无需导出")
            return result
    except OSError:
        pass

    in_place = False
    if dest.exists():
        if not force:
            result["status"] = "已存在，跳过（要覆盖加 --force）"
            rep.add("warn", f"{dest} 已存在，未改动")
            return result
        if dry:
            result["status"] = "已存在，--force 会先备份再覆盖（dry-run 未执行）"
            return result

        # 备份必须放在 skills/ 之外：放里面会被 Codex/ZCode 当成第二个同名技能加载
        backup_root = dest_root.with_name(dest_root.name + ".backup")
        backup = backup_root / f"{name}-{datetime.now():%Y%m%d-%H%M%S}"
        backup_root.mkdir(parents=True, exist_ok=True)
        try:
            dest.rename(backup)
        except PermissionError:
            # Windows 上 agent 正在运行时会盯着技能目录，改名被拒（WinError 5）。
            # 降级：先靠复制留下备份，再原地覆盖。
            try:
                shutil.copytree(dest, backup)
            except OSError as e:
                result["status"] = "备份失败，未改动"
                rep.add("fail", f"无法备份 {dest}（{e}）。请关闭正在运行的 agent 后重试")
                return result
            in_place = True
            rep.add(
                "warn",
                f"{dest} 被占用（agent 正在运行？），已改用「复制备份 + 原地覆盖」。"
                f"备份：{backup}",
            )
        else:
            rep.add("warn", f"原目录已备份到 {backup}")
    elif dry:
        result["status"] = "可导出（dry-run 未落盘）"
        result["files"] = sum(
            1 for p in SKILL_ROOT.rglob("*") if p.is_file() and should_copy(p.relative_to(SKILL_ROOT))
        )
        return result

    dest_root.mkdir(parents=True, exist_ok=True)
    copied: set[Path] = set()
    count = 0
    for src in sorted(SKILL_ROOT.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(SKILL_ROOT)
        if not should_copy(rel):
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        copied.add(rel)
        count += 1

    # 原地覆盖时，清掉源里已不存在的旧文件，避免残留过期脚本
    if in_place:
        stale = []
        for old in dest.rglob("*"):
            if not old.is_file():
                continue
            rel = old.relative_to(dest)
            if should_copy(rel) and rel not in copied:
                stale.append(rel)
                old.unlink()
        if stale:
            rep.add("warn", f"已清理 {len(stale)} 个源中不存在的旧文件：{[str(s) for s in stale[:3]]}")

    # 剔除宿主专有 frontmatter，避免别的 agent 因未知字段出问题。
    # 按字节处理：既要保留源文件的行尾符风格，也要保证其余内容逐字节不变。
    out_md = dest / "SKILL.md"
    lines = out_md.read_bytes().splitlines(keepends=True)
    stripped: list[str] = []
    if lines and lines[0].strip() == b"---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == b"---"), None)
        if end is not None:
            for i in range(1, end):
                key = lines[i].split(b":", 1)[0].strip().decode("utf-8", "ignore")
                if key in HOST_ONLY_KEYS:
                    stripped.append(key)
                    lines[i] = b""
    out_md.write_bytes(b"".join(lines))
    if stripped:
        rep.add("ok", f"{dest.name}: 已剔除宿主专有字段 {sorted(stripped)}")

    result["status"] = "已导出（原地覆盖）" if in_place else "已导出"
    result["files"] = count
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="把本技能导出到其他 agent 的技能目录")
    ap.add_argument(
        "--target",
        default="all",
        help="目标 agent，逗号分隔：codex,zcode,workbuddy 或 all（默认 all）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只预检，不落盘")
    ap.add_argument("--force", action="store_true", help="目标已存在时覆盖（先自动备份）")
    args = ap.parse_args()

    if args.target.strip().lower() == "all":
        names = list(TARGETS)
    else:
        names = [t.strip().lower() for t in args.target.split(",") if t.strip()]
        unknown = [t for t in names if t not in TARGETS]
        if unknown:
            ap.error(f"未知目标 {unknown}，可选：{', '.join(TARGETS)} 或 all")

    rep = Report()
    print(f"技能源目录: {SKILL_ROOT}\n")
    print("=== 可移植性预检 ===")
    name = preflight(rep)
    for m in rep.ok:
        print(f"  [v] {m}")
    for m in rep.warn:
        print(f"  [!] {m}")
    for m in rep.fail:
        print(f"  [x] {m}")

    if rep.fail:
        print(f"\n预检未通过（{len(rep.fail)} 项），已中止。先修问题再导出。")
        return 1

    # 预检阶段的信息已打印过，只汇报导出过程中新增的
    seen = (len(rep.ok), len(rep.warn), len(rep.fail))
    home = Path.home()
    print(f"\n=== 导出（{'dry-run，不落盘' if args.dry_run else '实际写入'}）===")
    results = []
    for key in names:
        rel, note = TARGETS[key]
        results.append((key, note, export(name, home / rel, args.force, args.dry_run, rep)))

    width = max(len(n) for n in names)
    for key, note, r in results:
        print(f"  {key:<{width}}  {r['status']:<34} {r['files']} 个文件")
        print(f"  {'':<{width}}  {r['target']}")
        print(f"  {'':<{width}}  调用方式: {note}")

    for m in rep.ok[seen[0]:]:
        print(f"  [v] {m}")
    for m in rep.warn[seen[1]:]:
        print(f"  [!] {m}")
    for m in rep.fail[seen[2]:]:
        print(f"  [x] {m}")

    if len(rep.fail) > seen[2]:
        print("\n有目标导出失败，其余目标不受影响。")
        return 1

    print("\n提示：新技能通常需要重启对应 agent 才会出现在技能列表里。")
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
    sys.exit(main())
