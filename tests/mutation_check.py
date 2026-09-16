"""变异测试：故意改坏 build_report.py，确认测试套件真的会失败。

一个「第一次运行就全绿」的测试套件不等于有效的测试套件——它可能只是在断言
永远不会发生的错误。这里对源码做定点破坏，每次破坏都**必须**导致测试失败；
如果某个变异后测试仍然全绿，说明那块逻辑没有被真正测到。

这个脚本已经抓到过一次真实漏洞：「权重和必须等于 1」那层校验被拆掉后，
原本的用例依然全绿——因为那条用例实际测的是「权重与 rubric 定值一致」。

跑法:
    python tests/mutation_check.py
退出码 0 表示全部变异被捕获。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

# (说明, 原文, 替换为) —— 原文必须在源码里只出现一次，否则跳过并计入未捕获
MUTATIONS = [
    (
        "拆掉证据基础上限（总分不再被截断）",
        "    if cap is not None and total > cap:",
        "    if False:",
    ),
    (
        "允许非法证据等级通过",
        "            if tier not in TIERS:",
        "            if False:",
    ),
    (
        "放弃权重和必须等于 1 的检查",
        "        if abs(weight_sum - 1.0) > 0.001:",
        "        if False:",
    ),
    (
        "放弃权重必须等于 rubric 定值的检查",
        "                if abs(float(weight) - expected) > 1e-9:",
        "                if False:",
    ),
    (
        "先取整再判级改成直接判级（显示分与定级脱节）",
        '    total = round(sum(float(d["score"]) * WEIGHTS[d["id"]] for d in dims), 1)',
        '    total = sum(float(d["score"]) * WEIGHTS[d["id"]] for d in dims)',
    ),
    (
        "E3 证据不再要求来源",
        '            if tier == "sourced":',
        "            if False:",
    ),
    (
        "E4/E5 不再要求 how_to_verify",
        '            if tier in ("inferred", "unknown"):',
        "            if False:",
    ),
    (
        "user_material 不再要求素材引用",
        '            if tier == "user_material" and not str(ev.get("material_ref") or "").strip():',
        "            if False:",
    ),
    (
        "A 层允许未验证走位混入",
        '            if "未验证" in step or "待验证" in step:',
        "            if False:",
    ),
    (
        "示意图不再强制标 is_illustration",
        '            if kind == "diagram" and not is_ill:',
        "            if False:",
    ),
    (
        "允许示意图冒充游戏画面",
        "            if is_ill and kind in REAL_IMAGE_KINDS:",
        "            if False:",
    ),
    (
        "校验失败后仍然继续渲染并返回 0",
        "        return 1",
        "        return 0",
    ),
    (
        "分数范围检查放宽（允许 1-10 以外的分）",
        "            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:",
        "            if False:",
    ),
    (
        "BOSS 阶段不再要求 tell/punish/risk",
        'for k in ("tell", "punish", "risk")',
        "for k in ()",
    ),
]


def classify(out: str) -> str:
    """区分「断言失败」和「语法/收集错误」。

    变异如果改坏了语法，pytest 会以收集错误退出——退出码同样非零，但**不代表
    测试抓到了逻辑缺陷**。把这种算作捕获是自欺欺人，必须单独识别。
    """
    if re.search(r"\d+ (?:failed|failures)", out):
        return "failed"
    if re.search(r"\d+ error", out) or "errors during collection" in out:
        return "error"
    return "passed"


def run_pytest(root: Path) -> tuple[str, str]:
    r = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/test_build_report.py",
            "-q", "--no-header", "-p", "no:cacheprovider",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    lines = (r.stdout or "").strip().splitlines()
    return classify(out), (lines[-1] if lines else out[-200:])


def main() -> int:
    src = SKILL / "scripts" / "build_report.py"
    original = src.read_text(encoding="utf-8")

    tmp = Path(tempfile.mkdtemp(prefix="mutcheck-"))
    work = tmp / "skill"
    shutil.copytree(
        SKILL, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "out")
    )
    target = work / "scripts" / "build_report.py"

    print("基线（未破坏）... ", end="", flush=True)
    status, note = run_pytest(work)
    print(("通过 " if status == "passed" else "未通过 ") + note)
    if status != "passed":
        print("[x] 基线未通过，后续结果无意义。先修测试。")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    print()

    survivors = []
    broken = []
    for i, (label, old, new) in enumerate(MUTATIONS, 1):
        hits = original.count(old)
        if hits != 1:
            print(f"{i:2}. [skip] 定位失败（出现 {hits} 次）: {label}")
            survivors.append(label)
            continue
        target.write_text(original.replace(old, new), encoding="utf-8")
        status, note = run_pytest(work)
        mark = {"failed": "被抓 [v]", "passed": "存活 [x]", "error": "无效 [!]"}[status]
        print(f"{i:2}. {mark}  {label}\n      {note}")
        if status == "passed":
            survivors.append(label)
        elif status == "error":
            broken.append(label)

    target.write_text(original, encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)

    caught = len(MUTATIONS) - len(survivors) - len(broken)
    print(f"\n结果：{caught}/{len(MUTATIONS)} 个变异被测试有效捕获")
    if broken:
        print("以下变异写出了语法错误——测试是「没跑起来」而非「抓到了」，不算数，请修正变异表达式：")
        for b in broken:
            print(f"  - {b}")
    if survivors:
        print("以下变异未被捕获，对应逻辑缺少有效测试覆盖：")
        for s in survivors:
            print(f"  - {s}")
    if survivors or broken:
        return 1
    print("全部变异均被捕获，且无无效变异。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
