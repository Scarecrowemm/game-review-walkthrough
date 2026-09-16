#!/usr/bin/env python3
"""校验 report-data.json -> 计算加权总分 -> 渲染评测 / 攻略两份 HTML。

这是本技能唯一允许生成 HTML 的入口。校验不通过时非零退出并列出全部问题，
不要手写 HTML 绕过。

用法:
    python build_report.py report-data.json --out ./out
    python build_report.py report-data.json --check
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

# ---------------------------------------------------------------- 常量

WEIGHTS = {
    "core_loop": 0.20,
    "level_design": 0.18,
    "feel_feedback": 0.15,
    "systems_balance": 0.14,
    "presentation": 0.12,
    "tech_stability": 0.11,
    "value_content": 0.10,
}

EVIDENCE_BASE = {
    "cleared_15h_plus": ("通关主线 · 时长 ≥ 15h · 素材可核", 10.0),
    "cleared_time_unverified": ("通关主线 · 时长或素材未获本地核实", 8.5),
    "cleared_under_15h": ("通关主线 · 时长 < 15h", 8.5),
    "core_loop_entered": ("未通关 · 已进入核心循环", 7.5),
    "early_under_3h": ("游玩 < 3h 或未进入核心循环", None),
    "research_only": ("未游玩 · 纯资料评测", 6.5),
}

TIERS = {"verified_play", "user_material", "sourced", "inferred", "unknown"}

TIER_BADGE = {
    "verified_play": "",
    "user_material": '<span class="badge badge-material">素材</span>',
    "sourced": "",
    "inferred": '<span class="badge badge-inferred">推断</span>',
    "unknown": '<span class="badge badge-unknown">未验证</span>',
}

VERDICT_TIERS = [
    (9.0, "标杆级"),
    (8.0, "强烈推荐"),
    (7.0, "推荐"),
    (6.0, "尚可"),
    (5.0, "谨慎"),
    (0.0, "不推荐"),
]

# 图片来源必须与证据等级对齐：能证明游戏里长什么样的，只有真实画面。
IMAGE_KINDS = {
    "user_capture": ("玩家截图/录像抽帧", ""),
    "official": ("官方素材", '<span class="badge badge-official">官方</span>'),
    "diagram": ("机制示意图", '<span class="badge badge-diagram">示意图</span>'),
}

# 只有 diagram 允许是非实拍内容。其余三类都必须指向真实存在的图像文件。
REAL_IMAGE_KINDS = {"user_capture", "official"}

DIAGRAM_WATERMARK = "示意图 · 非游戏画面"

DIAGRAM_TYPES = {"phase_timeline", "route_skeleton", "generic"}

LAYER_KEYS = ["A_main_route", "B_blockers", "C_bosses", "D_collectibles"]

LAYER_META = {
    "A_main_route": ("Layer A · 主线路线图", "章节级推进顺序、解锁能力与不可逆节点"),
    "B_blockers": ("Layer B · 卡关点", "机制拆解、思路、容错窗口与失败信号"),
    "C_bosses": ("Layer C · BOSS", "阶段表、出招读法、安全输出窗口"),
    "D_collectibles": ("Layer D · 隐藏要素", "位置、触发条件、可补性"),
}


# ---------------------------------------------------------------- helpers

def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def fmt_score(value: float) -> str:
    return f"{value:.1f}"


def verdict_for(total: float) -> str:
    for threshold, name in VERDICT_TIERS:
        if total >= threshold:
            return name
    return VERDICT_TIERS[-1][1]


# ---------------------------------------------------------------- 校验

class Validator:
    def __init__(self, data: dict, base_dir: Path | None = None):
        self.data = data
        self.base_dir = base_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.missing_images: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # -- 通用

    def check_evidence(self, evs, where: str) -> None:
        evs = as_list(evs)
        if not evs:
            self.err(f"{where}.evidence: 缺少 evidence 数组或数组为空")
            return
        for idx, ev in enumerate(evs):
            tag = f"{where}.evidence[{idx}]"
            if not isinstance(ev, dict):
                self.err(f"{tag}: 不是对象")
                continue
            tier = ev.get("tier")
            if tier not in TIERS:
                self.err(f"{tag}: tier 非法（{tier!r}），可选 {sorted(TIERS)}")
                continue
            if tier == "sourced":
                has_url = bool(str(ev.get("source_url") or "").strip())
                second = [s for s in as_list(ev.get("source")) if isinstance(s, dict) and s.get("url")]
                if not has_url and len(second) < 2:
                    self.err(
                        f"{tag}: tier=sourced 需要 source_url（一手来源）"
                        f"或 source 数组至少 2 条独立来源（二手来源）"
                    )
                if has_url and not str(ev.get("source_title") or "").strip():
                    self.warn(f"{tag}: 建议补 source_title，报告里链接才可读")
            if tier == "user_material" and not str(ev.get("material_ref") or "").strip():
                self.err(f"{tag}: tier=user_material 必须提供 material_ref（素材路径/时间戳）")
            if tier == "inferred" and not str(ev.get("basis") or "").strip():
                self.err(f"{tag}: tier=inferred 必须提供 basis（推导链）")
            if tier in ("inferred", "unknown"):
                htv = ev.get("how_to_verify")
                if not isinstance(htv, dict) or not all(
                    str(htv.get(k) or "").strip() for k in ("where", "what", "watch")
                ):
                    self.err(f"{tag}: tier={tier} 必须提供 how_to_verify{{where,what,watch}}")
            if tier == "unknown":
                self.warn(f"{tag}: 含未验证证据，报告将带红色徽标")

    # -- 各段

    def check_game(self) -> None:
        game = self.data.get("game")
        if not isinstance(game, dict):
            self.err("game: 缺失或不是对象")
            return
        for field in ("title", "platforms", "version", "review_date", "evidence_base", "disclosure"):
            if not game.get(field):
                self.err(f"game.{field}: 必填字段缺失")
        eb = game.get("evidence_base")
        if eb and eb not in EVIDENCE_BASE:
            self.err(f"game.evidence_base: 非法值 {eb!r}，可选 {sorted(EVIDENCE_BASE)}")
        if not game.get("version_confirmed", True):
            self.warn("game.version_confirmed=false：报告首屏将显示「版本未确认」")
        if not as_list(game.get("materials")):
            self.warn("game.materials: 为空。无可核素材时，所有结论最高只能到 E3")
        self.check_images(game.get("images"), "game")

    def check_review(self) -> None:
        review = self.data.get("review")
        if not isinstance(review, dict):
            self.err("review: 缺失或不是对象")
            return
        if not str(review.get("summary") or "").strip():
            self.err("review.summary: 必填")

        dims = as_list(review.get("dimensions"))
        if len(dims) != len(WEIGHTS):
            self.err(f"review.dimensions: 需要 {len(WEIGHTS)} 个维度，实际 {len(dims)} 个")

        seen = set()
        weight_sum = 0.0
        for idx, dim in enumerate(dims):
            tag = f"review.dimensions[{idx}]"
            if not isinstance(dim, dict):
                self.err(f"{tag}: 不是对象")
                continue
            did = dim.get("id")
            name = dim.get("name") or did
            if did not in WEIGHTS:
                self.err(f"{tag}: id 非法（{did!r}），可选 {sorted(WEIGHTS)}")
                continue
            if did in seen:
                self.err(f"{tag}: id 重复（{did}）")
            seen.add(did)
            if not str(dim.get("name") or "").strip():
                self.err(f"{tag}: 缺少 name")
            score = dim.get("score")
            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
                self.err(f"{tag}: score 必须是 1-10 的整数，实际 {score!r}")
            weight = dim.get("weight")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                self.err(f"{tag}: weight 必须是数字，实际 {weight!r}")
            else:
                weight_sum += float(weight)
                expected = WEIGHTS[did]
                if abs(float(weight) - expected) > 1e-9:
                    self.err(
                        f"{tag}: weight 与 rubric 固定值不一致（应为 {expected}，实际 {weight}）。"
                        f"权重全局固定，不得按品类调整"
                    )
            if not as_list(dim.get("plus")):
                self.err(f"{tag}: plus 至少需要一条加分锚点")
            if not as_list(dim.get("minus")):
                self.err(f"{tag}: minus 至少需要一条扣分锚点")
            self.check_evidence(dim.get("evidence"), tag)
            self.check_images(dim.get("images"), tag, allow_diagram=True)

        missing = set(WEIGHTS) - seen
        if missing:
            self.err(f"review.dimensions: 缺少维度 {sorted(missing)}")
        if abs(weight_sum - 1.0) > 0.001:
            self.err(f"review.dimensions: 权重和为 {weight_sum:.4f}，必须等于 1.00")

        bias = as_list(review.get("bias_audit"))
        if len(bias) < 5:
            self.err(f"review.bias_audit: 至少 5 条自查（当前 {len(bias)} 条）")
        for idx, item in enumerate(bias):
            if not isinstance(item, dict) or not all(
                str(item.get(k) or "").strip() for k in ("risk", "finding", "action")
            ):
                self.err(f"review.bias_audit[{idx}]: 需要 risk / finding / action 三字段")

        if len(as_list(review.get("pros"))) < 3:
            self.err("review.pros: 至少 3 条")
        if len(as_list(review.get("cons"))) < 3:
            self.err("review.cons: 至少 3 条")

        for idx, src in enumerate(as_list(review.get("sources"))):
            if not isinstance(src, dict) or not str(src.get("url") or "").strip():
                self.err(f"review.sources[{idx}]: 需要 title 与 url")

    def check_walkthrough(self) -> None:
        wt = self.data.get("walkthrough")
        if not isinstance(wt, dict):
            self.err("walkthrough: 缺失或不是对象")
            return
        if not str(wt.get("scope") or "").strip():
            self.err("walkthrough.scope: 必填（覆盖范围必须写清）")
        layers = wt.get("layers")
        if not isinstance(layers, dict):
            self.err("walkthrough.layers: 缺失或不是对象")
            return
        for key in LAYER_KEYS:
            entries = as_list(layers.get(key))
            if not entries:
                self.err(f"walkthrough.layers.{key}: 为空。四层都必须有内容，无内容时也应写明「本作无此类要素」")
                continue
            for idx, entry in enumerate(entries):
                tag = f"walkthrough.layers.{key}[{idx}]"
                if not isinstance(entry, dict):
                    self.err(f"{tag}: 不是对象")
                    continue
                self.check_evidence(entry.get("evidence"), tag)
                self.check_images(entry.get("images"), tag, allow_diagram=True)
                if key in ("A_main_route", "C_bosses") and not as_list(entry.get("images")):
                    self.warn(
                        f"{tag}: 无配图。攻略条目建议至少配一张图；"
                        f"无截图时可用 kind=diagram 自动生成阶段/路线示意图"
                    )
                if key in ("B_blockers", "C_bosses"):
                    if not str(entry.get("tolerance") or entry.get("phases") or "").strip():
                        if not as_list(entry.get("phases")):
                            self.err(f"{tag}: B/C 层条目必须提供容错窗口（tolerance）或阶段表（phases）")
                if key == "C_bosses":
                    for pidx, phase in enumerate(as_list(entry.get("phases"))):
                        if not isinstance(phase, dict) or not all(
                            str(phase.get(k) or "").strip()
                            for k in ("tell", "punish", "risk")
                        ):
                            self.err(
                                f"{tag}.phases[{pidx}]: 每个阶段必须提供 tell / punish / risk"
                            )
                if key == "A_main_route":
                    self.check_no_unknown_steps(entry, tag)

        for idx, item in enumerate(as_list(wt.get("unverified"))):
            tag = f"walkthrough.unverified[{idx}]"
            if not isinstance(item, dict):
                self.err(f"{tag}: 不是对象")
                continue
            if not str(item.get("question") or "").strip():
                self.err(f"{tag}: 缺少 question")
            htv = item.get("how_to_verify")
            if not isinstance(htv, dict) or not all(
                str(htv.get(k) or "").strip() for k in ("where", "what", "watch")
            ):
                self.err(f"{tag}: how_to_verify 需要 where / what / watch 三字段全填")

    def check_no_unknown_steps(self, entry: dict, tag: str) -> None:
        """A 层正文不得出现祈使句式的未验证走位。"""
        import re as _re
        imperative = _re.compile(r"^(先|然后|接着|往|向|跳|走|按|打开|使用|前往|返回|攻击|躲避)")
        for sidx, step in enumerate(as_list(entry.get("route"))):
            if not isinstance(step, str):
                continue
            if "未验证" in step or "待验证" in step:
                self.err(
                    f"{tag}.route[{sidx}]: 未验证内容不得混入 A 层推进路线，"
                    f"请移入 walkthrough.unverified"
                )
            if imperative.match(step.strip()) and "[推断]" not in step:
                self.warn(
                    f"{tag}.route[{sidx}]: 「{step[:16]}…」是祈使句走位。"
                    f"A 层应为章节级目标，细节走位请下沉到 B 层"
                )

    # -- 图片

    def resolve_image(self, path: str) -> Path | None:
        raw = str(path or "").strip()
        if not raw:
            return None
        p = Path(raw).expanduser()
        if not p.is_absolute() and self.base_dir:
            p = self.base_dir / p
        return p

    def check_images(self, images, where: str, *, allow_diagram: bool = True) -> None:
        images = as_list(images)
        if not images:
            return
        for idx, img in enumerate(images):
            tag = f"{where}.images[{idx}]"
            if not isinstance(img, dict):
                self.err(f"{tag}: 不是对象")
                continue
            kind = img.get("kind")
            if kind not in IMAGE_KINDS:
                self.err(
                    f"{tag}: kind 非法（{kind!r}），可选 {sorted(IMAGE_KINDS)}。"
                    f"AI 生成的图不得冒充游戏画面，机制示意请用 diagram"
                )
                continue
            if kind == "diagram" and not allow_diagram:
                self.err(f"{tag}: 此位置不接受 diagram")
            if not str(img.get("caption") or "").strip():
                self.err(f"{tag}: caption 必填。图注要说明「这张图让你看什么」，无图注的图等于噪音")

            is_ill = bool(img.get("is_illustration"))
            if is_ill and kind in REAL_IMAGE_KINDS:
                self.err(
                    f"{tag}: is_illustration=true 的图不能标为 {kind}（游戏画面/官方素材）。"
                    f"示意图请用 kind=diagram，否则等于伪造游戏截图"
                )
            if kind == "diagram" and not is_ill:
                self.err(f"{tag}: kind=diagram 必须显式写 is_illustration=true")
            if kind == "official":
                if not str(img.get("source_url") or "").strip():
                    self.err(f"{tag}: 官方素材必须提供 source_url")
                if not str(img.get("credit") or "").strip():
                    self.err(f"{tag}: 官方素材必须提供 credit（版权归属）")

            if kind in REAL_IMAGE_KINDS:
                resolved = self.resolve_image(img.get("path"))
                if resolved is None:
                    self.err(f"{tag}: kind={kind} 必须提供 path")
                elif not resolved.is_file():
                    self.warn(
                        f"{tag}: 图片文件不存在（{resolved}），报告将渲染为「待补图」占位框"
                    )
                    self.missing_images.append(f"{where} · {img.get('caption')}")

            dtype = img.get("diagram_type")
            if kind == "diagram" and dtype and dtype not in DIAGRAM_TYPES:
                self.err(f"{tag}: diagram_type 非法（{dtype!r}），可选 {sorted(DIAGRAM_TYPES)}")

    def run(self) -> None:
        self.check_game()
        self.check_review()
        self.check_walkthrough()


# ---------------------------------------------------------------- 渲染片段

def render_evidence(evs, *, compact: bool = False) -> str:
    evs = as_list(evs)
    if not evs:
        return ""
    rows = []
    for ev in evs:
        if not isinstance(ev, dict):
            continue
        tier = ev.get("tier", "unknown")
        badge = TIER_BADGE.get(tier, "")
        bits: list[str] = []
        if ev.get("note"):
            bits.append(esc(ev["note"]))
        if ev.get("material_ref"):
            bits.append(f'<code>{esc(ev["material_ref"])}</code>')
        if ev.get("basis"):
            bits.append(f'<span class="basis">推导链：{esc(ev["basis"])}</span>')
        if ev.get("source_url"):
            label = ev.get("source_title") or ev["source_url"]
            bits.append(f'<a href="{esc(ev["source_url"])}" target="_blank" rel="noopener">{esc(label)}</a>')
        for src in as_list(ev.get("source")):
            if isinstance(src, dict) and src.get("url"):
                bits.append(
                    f'<a href="{esc(src["url"])}" target="_blank" rel="noopener">'
                    f'{esc(src.get("title") or src["url"])}</a>'
                )
        htv = ev.get("how_to_verify")
        if isinstance(htv, dict):
            bits.append(
                f'<span class="verify">验证：{esc(htv.get("where",""))} → '
                f'{esc(htv.get("what",""))} → 观察 {esc(htv.get("watch",""))}</span>'
            )
        if not bits:
            bits.append(esc(tier))
        rows.append(f'<li class="ev-item">{badge}{" · ".join(bits)}</li>')
    if not rows:
        return ""
    cls = "ev compact" if compact else "ev"
    return f'<ul class="{cls}">' + "".join(rows) + "</ul>"


def resolve_image_path(path, base_dir) -> Path | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute() and base_dir:
        p = Path(base_dir) / p
    return p


def image_src(img: dict, cfg: dict) -> tuple[str, bool]:
    """把图片落成 <img src> 可用的地址。返回 (src, 是否存在)。"""
    resolved = resolve_image_path(img.get("path"), cfg.get("base_dir"))
    if resolved is None or not resolved.is_file():
        return "", False

    mode = cfg.get("mode", "copy")
    if mode == "inline":
        data = resolved.read_bytes()
        mime = MIME_BY_SUFFIX.get(resolved.suffix.lower(), "application/octet-stream")
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", True
    if mode == "link":
        return resolved.as_uri(), True

    dest_dir = Path(cfg["out_dir"]) / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / resolved.name
    if dest.exists():
        try:
            same = dest.stat().st_size == resolved.stat().st_size
        except OSError:
            same = False
        if not same:
            dest = dest_dir / f"{abs(hash(str(resolved))) % 100000}_{resolved.name}"
    if not dest.exists():
        shutil.copy2(resolved, dest)
    rel = os.path.relpath(dest, Path(cfg["out_dir"])).replace("\\", "/")
    return rel, True


def missing_image_box(img: dict) -> str:
    kind = img.get("kind", "")
    label = IMAGE_KINDS.get(kind, ("未知", ""))[0]
    path = str(img.get("path") or "").strip()
    hint = img.get("how_to_get") or f"放入文件：{path}" if path else "补充 path 字段"
    return (
        '<figure class="fig fig-missing">'
        '<div class="fig-missing-body">'
        '<span class="fig-missing-tag">待补图</span>'
        f'<div class="fig-missing-what">{esc(img.get("caption"))}</div>'
        f'<div class="fig-missing-meta">需要：{esc(label)}'
        + (f' ｜ <code>{esc(path)}</code>' if path else "")
        + "</div>"
        f'<div class="fig-missing-hint">{esc(hint)}</div>'
        "</div></figure>"
    )


def parse_hp_range(text: str) -> tuple[float, float] | None:
    """解析 '100%-65%' / '100-65' 这类血量区间。"""
    nums = re.findall(r"(\d+(?:\.\d+)?)", str(text or ""))
    if len(nums) < 2:
        return None
    a, b = float(nums[0]), float(nums[1])
    if a > 100 or b > 100:
        return None
    lo, hi = min(a, b), max(a, b)
    return lo, hi


def phase_timeline_svg(phases: list) -> str:
    """把阶段表画成血量进度条。色块宽度 = 血量占比，不虚构任何坐标。"""
    segs = []
    for idx, p in enumerate(phases):
        if not isinstance(p, dict):
            continue
        rng = parse_hp_range(p.get("hp_range"))
        if rng is None:
            continue
        lo, hi = rng
        segs.append((idx, p, lo, hi))
    if not segs:
        return ""

    w, bar_y, bar_h = 560, 46, 36
    x0, x1 = 20, w - 20
    span = x1 - x0

    def x_of(hp: float) -> float:
        return x0 + span * (100 - hp) / 100

    parts = [f'<svg class="diagram" viewBox="0 0 {w} {bar_y + bar_h + 34}" role="img" aria-label="BOSS 阶段血量进度示意">']
    for i, (_idx, p, lo, hi) in enumerate(segs):
        sx, ex = x_of(hi), x_of(lo)
        cls = "seg seg-alt" if i % 2 else "seg"
        parts.append(
            f'<rect x="{sx:.1f}" y="{bar_y}" width="{max(ex - sx, 1):.1f}" height="{bar_h}" '
            f'rx="4" class="{cls}"/>'
        )
        label = f"P{esc(p.get('phase'))} {hi:g}-{lo:g}%"
        parts.append(
            f'<text x="{(sx + ex) / 2:.1f}" y="{bar_y + bar_h / 2 + 5:.1f}" '
            f'text-anchor="middle" class="seg-label">{label}</text>'
        )
    parts.append(f'<line x1="{x0}" y1="{bar_y + bar_h + 8}" x2="{x1}" y2="{bar_y + bar_h + 8}" class="axis"/>')
    parts.append(f'<text x="{x0}" y="{bar_y - 14}" class="seg-note">100% 血量</text>')
    parts.append(f'<text x="{x1}" y="{bar_y - 14}" text-anchor="end" class="seg-note">击杀</text>')
    parts.append(
        f'<text x="{x0}" y="{bar_y + bar_h + 26}" class="seg-note">'
        f'色块宽度按血量区间绘制；出招读法与输出窗口见下表</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def route_skeleton_svg(entries: list) -> str:
    """章节推进骨架。等距排布，不表示真实时长。"""
    rows = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rows.append(e)
    if not rows:
        return ""

    row_h, pad_top = 62, 26
    w = 560
    h = pad_top * 2 + row_h * (len(rows) - 1) + 20
    rail_x, node_r = 34, 7

    parts = [f'<svg class="diagram" viewBox="0 0 {w} {h}" role="img" aria-label="主线章节推进骨架">']
    y_first = pad_top
    y_last = pad_top + row_h * (len(rows) - 1)
    parts.append(f'<line x1="{rail_x}" y1="{y_first}" x2="{rail_x}" y2="{y_last}" class="rail"/>')
    for i, e in enumerate(rows):
        y = pad_top + row_h * i
        parts.append(f'<circle cx="{rail_x}" cy="{y}" r="{node_r}" class="node"/>')
        parts.append(f'<text x="{rail_x + 22}" y="{y + 5}" class="node-title">{esc(e.get("chapter"))}</text>')
        sub = " · ".join(x for x in [str(e.get("goal") or ""), str(e.get("time_estimate") or "")] if x)
        if sub:
            parts.append(f'<text x="{rail_x + 22}" y="{y + 24}" class="node-sub">{esc(sub[:46])}</text>')
        miss = as_list(e.get("missable"))
        if miss:
            parts.append(
                f'<text x="{rail_x + 22}" y="{y + 41}" class="node-warn">'
                f'含 {len(miss)} 个不可逆节点</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def render_figures(images, cfg: dict, *, phases=None, route=None, auto_label: str = "") -> str:
    images = [i for i in as_list(images) if isinstance(i, dict)]
    figs: list[str] = []
    declared_diagrams = {i.get("diagram_type") for i in images if i.get("kind") == "diagram"}

    for img in images:
        kind = img.get("kind", "diagram")
        _label, badge = IMAGE_KINDS.get(kind, ("", ""))
        caption = esc(img.get("caption"))
        metas: list[str] = []
        if img.get("is_illustration"):
            metas.append(f'<span class="wm">{DIAGRAM_WATERMARK}</span>')
        if img.get("credit"):
            metas.append(esc(img["credit"]))
        if img.get("timestamp"):
            metas.append("时间戳 " + esc(img["timestamp"]))
        if img.get("source_url"):
            metas.append(
                f'<a href="{esc(img["source_url"])}" target="_blank" rel="noopener">来源</a>'
            )
        anchors = "".join(f"<li>{esc(a)}</li>" for a in as_list(img.get("anchors")))
        anchor_html = f'<ul class="fig-anchors">{anchors}</ul>' if anchors else ""

        if kind == "diagram":
            dtype = img.get("diagram_type") or "generic"
            if dtype == "phase_timeline" and phases:
                body = phase_timeline_svg(phases)
            elif dtype == "route_skeleton" and route:
                body = route_skeleton_svg(route)
            else:
                body = f'<div class="fig-generic">{DIAGRAM_WATERMARK}</div>'
            figcls = "fig fig-diagram"
        else:
            src, ok = image_src(img, cfg)
            if ok:
                body = f'<img src="{esc(src)}" alt="{caption}" loading="lazy">'
            else:
                figs.append(missing_image_box(img))
                continue
            figcls = "fig fig-real"

        figs.append(
            f'<figure class="{figcls}">{body}'
            f'<figcaption>{badge}<span class="cap">{caption}</span>'
            + (f'<span class="fig-meta">{" · ".join(metas)}</span>' if metas else "")
            + anchor_html
            + "</figcaption></figure>"
        )

    # 有阶段表/章节表但没声明对应的示意图时，自动补一张——数据已经在了，不需要用户再画
    if phases and "phase_timeline" not in declared_diagrams:
        svg = phase_timeline_svg(phases)
        if svg:
            figs.append(
                f'<figure class="fig fig-diagram">{svg}'
                f'<figcaption><span class="badge badge-diagram">示意图</span>'
                f'<span class="cap">阶段与血量区间（由阶段表自动生成）</span>'
                f'<span class="fig-meta"><span class="wm">{DIAGRAM_WATERMARK}</span></span>'
                f"</figcaption></figure>"
            )
    if route and "route_skeleton" not in declared_diagrams:
        svg = route_skeleton_svg(route)
        if svg:
            figs.append(
                f'<figure class="fig fig-diagram">{svg}'
                f'<figcaption><span class="badge badge-diagram">示意图</span>'
                f'<span class="cap">章节推进骨架（等距排布，不表示真实时长）</span>'
                f'<span class="fig-meta"><span class="wm">{DIAGRAM_WATERMARK}</span></span>'
                f"</figcaption></figure>"
            )

    if not figs:
        return ""
    label = f'<div class="fig-group-label">配图</div>' if auto_label else ""
    return label + '<div class="figs">' + "".join(figs) + "</div>"


def render_radar(dims: list[dict]) -> str:
    n = len(dims)
    if n < 3:
        return ""
    w, h = 560, 420
    cx, cy, R = 280, 210, 118

    def pt(i: int, ratio: float) -> tuple[float, float]:
        a = -math.pi / 2 + i * 2 * math.pi / n
        return cx + R * ratio * math.cos(a), cy + R * ratio * math.sin(a)

    out = [f'<svg class="radar" viewBox="0 0 {w} {h}" role="img" aria-label="维度评分雷达图">']
    for lvl in (2, 4, 6, 8, 10):
        pts = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (pt(i, lvl / 10) for i in range(n))
        )
        cls = "ring ring-outer" if lvl == 10 else "ring"
        out.append(f'<polygon points="{pts}" class="{cls}"/>')
    for i in range(n):
        x, y = pt(i, 1.0)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" class="axis"/>')

    data_pts = []
    for i, dim in enumerate(dims):
        score = dim.get("score")
        score = score if isinstance(score, int) else 5
        data_pts.append(pt(i, max(0.02, min(score, 10) / 10)))
    out.append(
        '<polygon points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in data_pts)
        + '" class="area"/>'
    )
    for x, y in data_pts:
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot"/>')

    for i, dim in enumerate(dims):
        a = -math.pi / 2 + i * 2 * math.pi / n
        dx, dy = math.cos(a), math.sin(a)
        x, y = pt(i, 1.0)
        lx, ly = x + dx * 28, y + dy * 24
        anchor = "middle"
        if dx > 0.3:
            anchor = "start"
        elif dx < -0.3:
            anchor = "end"
        out.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" class="rlabel">'
            f'{esc(dim.get("name") or dim.get("id"))}</text>'
        )
        out.append(
            f'<text x="{lx:.1f}" y="{ly + 15:.1f}" text-anchor="{anchor}" class="rscore">'
            f'{esc(dim.get("score"))}/10</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def dim_band(score: int) -> str:
    if score >= 8:
        return "band-good"
    if score >= 6:
        return "band-mid"
    return "band-low"


def render_dimension_cards(dims: list[dict], cfg: dict) -> str:
    cards = []
    for dim in dims:
        if not isinstance(dim, dict):
            continue
        score = dim.get("score")
        score = score if isinstance(score, int) else 0
        weight = dim.get("weight")
        weight_txt = f"{float(weight) * 100:.0f}%" if isinstance(weight, (int, float)) else "—"
        contribution = f"{score * float(weight):.2f}" if isinstance(weight, (int, float)) else "—"
        plus = "".join(f"<li>{esc(x)}</li>" for x in as_list(dim.get("plus")))
        minus = "".join(f"<li>{esc(x)}</li>" for x in as_list(dim.get("minus")))
        coverage = (
            f'<div class="coverage">覆盖范围：{esc(dim["coverage_note"])}</div>'
            if dim.get("coverage_note")
            else ""
        )
        cards.append(
            f"""<section class="dim {dim_band(score)}">
  <header>
    <h3>{esc(dim.get('name') or dim.get('id'))}</h3>
    <div class="dim-score"><strong>{score}</strong><span>/10</span></div>
  </header>
  <div class="dim-meta">权重 {weight_txt} · 贡献 {contribution} 分</div>
  {coverage}
  <div class="anchors">
    <div class="anchor-plus"><h4>加分锚点</h4><ul>{plus}</ul></div>
    <div class="anchor-minus"><h4>扣分锚点</h4><ul>{minus}</ul></div>
  </div>
  {render_figures(dim.get('images'), cfg)}
  {render_evidence(dim.get('evidence'))}
</section>"""
        )
    return "\n".join(cards)


def render_bias_table(items: list) -> str:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"<tr><td class=\"risk\">{esc(item.get('risk'))}</td>"
            f"<td>{esc(item.get('finding'))}</td>"
            f"<td>{esc(item.get('action'))}</td></tr>"
        )
    return "\n".join(rows)


def render_sources(sources: list) -> str:
    rows = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        kind = esc(src.get("kind") or "source")
        rows.append(
            f'<li><span class="tag">{kind}</span>'
            f'<a href="{esc(src.get("url"))}" target="_blank" rel="noopener">'
            f'{esc(src.get("title") or src.get("url"))}</a>'
            f'<span class="retrieved">检索于 {esc(src.get("retrieved"))}</span></li>'
        )
    return "\n".join(rows)


def render_layer_a(entries: list, cfg: dict) -> str:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        route = "".join(f"<li>{esc(s)}</li>" for s in as_list(e.get("route")))
        unlock = "".join(f'<span class="chip">{esc(u)}</span>' for u in as_list(e.get("unlock")))
        missable = ""
        if as_list(e.get("missable")):
            items = []
            for m in as_list(e["missable"]):
                if isinstance(m, dict):
                    rec = "可补" if m.get("recoverable") else "不可补"
                    items.append(
                        f'<li>{esc(m.get("what"))} · 时机 {esc(m.get("window"))} · '
                        f'<strong class="{"ok" if m.get("recoverable") else "no"}">{rec}</strong></li>'
                    )
                else:
                    items.append(f"<li>{esc(m)}</li>")
            missable = f'<div class="missable"><h4>不可逆节点</h4><ul>{"".join(items)}</ul></div>'
        out.append(
            f"""<section class="layer-card">
  <div class="layer-head"><h3>{esc(e.get('chapter'))}</h3>
    <span class="time">{esc(e.get('time_estimate') or '时长未验证')}</span></div>
  <p class="goal">{esc(e.get('goal'))}</p>
  <ol class="route">{route}</ol>
  {f'<div class="chips">{unlock}</div>' if unlock else ''}
  {missable}
  {render_figures(e.get('images'), cfg)}
  {render_evidence(e.get('evidence'))}
</section>"""
        )
    return "\n".join(out)


def render_layer_b(entries: list, cfg: dict) -> str:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        sol = e.get("solution") if isinstance(e.get("solution"), dict) else {}
        steps = "".join(f"<li>{esc(s)}</li>" for s in as_list(sol.get("steps")))
        errs = "".join(f"<li>{esc(s)}</li>" for s in as_list(sol.get("common_errors")))
        out.append(
            f"""<section class="layer-card">
  <div class="layer-head"><h3>{esc(e.get('title'))}</h3>
    <span class="time">{esc(e.get('chapter'))}</span></div>
  <p class="symptom"><span class="label">失败信号</span>{esc(e.get('symptom'))}</p>
  <p class="mechanic"><span class="label">在考什么</span>{esc(e.get('mechanic'))}</p>
  <p class="idea"><span class="label">思路</span>{esc(sol.get('思路') or sol.get('idea'))}</p>
  <ol class="route">{steps}</ol>
  <p class="tolerance"><span class="label">容错窗口</span>{esc(e.get('tolerance') or '未验证')}</p>
  {f'<div class="errors"><h4>常见错误</h4><ul>{errs}</ul></div>' if errs else ''}
  {render_figures(e.get('images'), cfg)}
  {render_evidence(e.get('evidence'))}
</section>"""
        )
    return "\n".join(out)


def render_layer_c(entries: list, cfg: dict) -> str:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rec = e.get("recommended") if isinstance(e.get("recommended"), dict) else {}
        rec_html = "".join(
            f'<div><span class="label">{esc(k)}</span>{esc(v)}</div>'
            for k, v in (("等级/战力", rec.get("level")), ("装备", rec.get("gear")), ("道具", rec.get("consumables")))
            if v
        )
        phases_data = as_list(e.get("phases"))
        phases = []
        for p in phases_data:
            if not isinstance(p, dict):
                continue
            phases.append(
                f"""<tr>
  <td class="ph">{esc(p.get('phase'))}</td>
  <td>{esc(p.get('hp_range'))}</td>
  <td>{esc(p.get('pattern'))}</td>
  <td class="tell">{esc(p.get('tell'))}</td>
  <td class="punish">{esc(p.get('punish'))}</td>
  <td class="risk">{esc(p.get('risk'))}</td>
</tr>"""
            )
        out.append(
            f"""<section class="layer-card">
  <div class="layer-head"><h3>{esc(e.get('name'))}</h3>
    <span class="time">{esc(e.get('chapter'))}</span></div>
  {f'<div class="recommended">{rec_html}</div>' if rec_html else ''}
  <table class="phases">
    <thead><tr><th>阶段</th><th>血量区间</th><th>招式循环</th><th>出招读法</th><th>安全输出窗口</th><th>低容错点</th></tr></thead>
    <tbody>{''.join(phases)}</tbody>
  </table>
  <p class="cheese"><span class="label">低风险打法</span>{esc(e.get('cheese') or '无')}</p>
  {render_figures(e.get('images'), cfg, phases=phases_data)}
  {render_evidence(e.get('evidence'))}
</section>"""
        )
    return "\n".join(out)


def render_layer_d(entries: list, cfg: dict) -> str:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        missable = e.get("missable")
        flag = (
            '<span class="badge badge-unknown">错过不可逆</span>'
            if missable
            else '<span class="badge badge-material">可随时获取</span>'
        )
        out.append(
            f"""<section class="layer-card">
  <div class="layer-head"><h3>{esc(e.get('name'))} {flag}</h3>
    <span class="time">{esc(e.get('chapter'))}</span></div>
  <div class="kv">
    <div><span class="label">位置</span>{esc(e.get('location'))}</div>
    <div><span class="label">触发条件</span>{esc(e.get('trigger'))}</div>
    <div><span class="label">可否弥补</span>{esc(e.get('recoverable'))}</div>
    <div><span class="label">奖励</span>{esc(e.get('value'))}</div>
  </div>
  {render_figures(e.get('images'), cfg)}
  {render_evidence(e.get('evidence'))}
</section>"""
        )
    return "\n".join(out)


def render_unverified(items: list) -> str:
    if not items:
        return '<p class="empty">无待验证条目。</p>'
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        htv = it.get("how_to_verify") if isinstance(it.get("how_to_verify"), dict) else {}
        pri = str(it.get("priority") or "low").lower()
        out.append(
            f"""<li class="unv {esc(pri)}">
  <div class="unv-head"><span class="pri pri-{esc(pri)}">{esc(pri)}</span>{esc(it.get('question'))}</div>
  <div class="unv-body">
    <div><span class="label">在哪</span>{esc(htv.get('where'))}</div>
    <div><span class="label">试什么</span>{esc(htv.get('what'))}</div>
    <div><span class="label">看什么</span>{esc(htv.get('watch'))}</div>
  </div>
</li>"""
        )
    return '<ul class="unv-list">' + "".join(out) + "</ul>"


# ---------------------------------------------------------------- 渲染总装

def build_context(data: dict) -> dict:
    game = data["game"]
    review = data["review"]
    wt = data["walkthrough"]

    dims = review["dimensions"]
    # 先四舍五入到 1 位，再据此判级：报告上显示的分与定级必须一致，
    # 否则会出现「显示 7.0 但定级是尚可」这种自相矛盾（6.99 的浮点尾巴导致）。
    total = round(sum(float(d["score"]) * WEIGHTS[d["id"]] for d in dims), 1)

    eb_key = game.get("evidence_base")
    eb_label, cap = EVIDENCE_BASE.get(eb_key, ("未确认", None))
    capped = False
    if cap is not None and total > cap:
        total = cap
        capped = True
    no_total = cap is None

    warnings = []
    if not game.get("version_confirmed", True):
        warnings.append("版本未确认：结论可能随补丁变化，未标注版本的判断请勿引用。")
    if capped:
        warnings.append(
            f"证据基础上限生效：原始加权总分高于 {cap}，已截断至 {cap}。"
            f"提升上限需要更完整的游玩证据。"
        )
    if not game.get("materials"):
        warnings.append("未提供可核素材：所有结论最高只能到 E3（有源），不存在 E1 级证据。")

    return {
        "game": game,
        "review": review,
        "wt": wt,
        "dims": dims,
        "total": total,
        "no_total": no_total,
        "eb_label": eb_label,
        "verdict": verdict_for(total) if not no_total else "不出定级",
        "warnings": warnings,
    }


def render_review(ctx: dict, template: str, css: str, cfg: dict) -> str:
    game, review = ctx["game"], ctx["review"]
    total = ctx["total"]

    meta_bits = [
        " / ".join(as_list(game.get("platforms"))),
        f"v{game.get('version')}" + ("" if game.get("version_confirmed", True) else "（未确认）"),
        game.get("language") or "",
        f"评测日 {game.get('review_date')}",
    ]
    meta = " ｜ ".join(b for b in meta_bits if b)

    if ctx["no_total"]:
        score_block = (
            '<div class="score-block no-total">'
            '<div class="score-value">—</div>'
            '<div class="score-side"><div class="verdict">不出总分</div>'
            '<div class="note">证据基础不足，仅输出维度分</div></div></div>'
        )
    else:
        score_block = (
            f'<div class="score-block">'
            f'<div class="score-value">{fmt_score(total)}</div>'
            f'<div class="score-side"><div class="verdict">{esc(ctx["verdict"])}</div>'
            f'<div class="note">七维加权 · 权重固定 · 可重算</div></div></div>'
        )

    warn_html = "".join(f"<li>{esc(w)}</li>" for w in ctx["warnings"])
    banner = (
        f'<div class="banner"><div class="banner-line">'
        f'<span class="label">证据基础</span>{esc(ctx["eb_label"])}</div>'
        f'<div class="banner-line"><span class="label">声明</span>{esc(game.get("disclosure"))}</div>'
        f'<div class="banner-line"><span class="label">完成度</span>{esc(game.get("completion") or "未提供")}'
        f' ｜ 游玩 {esc(game.get("playtime_hours") or "未提供")} 小时</div>'
        + (f'<ul class="warns">{warn_html}</ul>' if warn_html else "")
        + "</div>"
    )

    series_note = review.get("series_note")
    series_html = (
        f'<section class="block"><h2>系列对比（不计入总分）</h2><p>{esc(series_note)}</p></section>'
        if series_note
        else ""
    )

    pros = "".join(f"<li>{esc(x)}</li>" for x in as_list(review.get("pros")))
    cons = "".join(f"<li>{esc(x)}</li>" for x in as_list(review.get("cons")))

    mapping = {
        "CSS": css,
        "GAME_TITLE": esc(game.get("title")),
        "GAME_META": esc(meta),
        "TITLE_EN": esc(game.get("title_en") or ""),
        "BANNER": banner,
        "COVER": render_figures(game.get("images"), cfg),
        "SCORE_BLOCK": score_block,
        "RADAR": render_radar(ctx["dims"]),
        "SUMMARY": esc(review.get("summary")),
        "DIMENSION_CARDS": render_dimension_cards(ctx["dims"], cfg),
        "PROS": pros,
        "CONS": cons,
        "SERIES_NOTE": series_html,
        "BIAS_AUDIT": render_bias_table(review.get("bias_audit")),
        "SOURCES": render_sources(review.get("sources")),
        "GENERATED_AT": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return fill(template, mapping)


def render_walkthrough(ctx: dict, template: str, css: str, cfg: dict) -> str:
    game, wt = ctx["game"], ctx["wt"]
    layers = wt["layers"]
    meta_bits = [
        " / ".join(as_list(game.get("platforms"))),
        f"v{game.get('version')}" + ("" if game.get("version_confirmed", True) else "（未确认）"),
        f"整理日 {game.get('review_date')}",
    ]

    sections = []
    for key in LAYER_KEYS:
        title, subtitle = LAYER_META[key]
        entries = as_list(layers.get(key))
        renderer = {
            "A_main_route": render_layer_a,
            "B_blockers": render_layer_b,
            "C_bosses": render_layer_c,
            "D_collectibles": render_layer_d,
        }[key]
        body = renderer(entries, cfg) if entries else '<p class="empty">本作无此类要素。</p>'
        if key == "A_main_route" and entries:
            # 章节骨架放在本层最前，让玩家先看清全貌再往下看单章细节
            body = render_figures([], cfg, route=entries) + body
        sections.append(
            f'<section class="layer" id="{key}">'
            f'<div class="layer-title"><h2>{title}</h2><p>{subtitle}'
            f'<span class="count">{len(entries)} 条</span></p></div>{body}</section>'
        )

    nav = "".join(
        f'<a href="#{k}">{LAYER_META[k][0].split(" · ")[1]}</a>' for k in LAYER_KEYS
    )

    banner = (
        f'<div class="banner"><div class="banner-line">'
        f'<span class="label">覆盖范围</span>{esc(wt.get("scope"))}</div>'
        f'<div class="banner-line"><span class="label">证据基础</span>{esc(ctx["eb_label"])}'
        f' ｜ 游玩 {esc(game.get("playtime_hours") or "未提供")} 小时</div></div>'
    )

    mapping = {
        "CSS": css,
        "GAME_TITLE": esc(game.get("title")),
        "GAME_META": esc(" ｜ ".join(b for b in meta_bits if b)),
        "BANNER": banner,
        "COVER": render_figures(game.get("images"), cfg),
        "NAV": nav,
        "SCOPE": esc(wt.get("scope")),
        "LAYER_A": sections[0],
        "LAYER_B": sections[1],
        "LAYER_C": sections[2],
        "LAYER_D": sections[3],
        "UNVERIFIED": render_unverified(as_list(wt.get("unverified"))),
        "SOURCES": render_sources(ctx["review"].get("sources")),
        "GENERATED_AT": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return fill(template, mapping)


PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def fill(template: str, mapping: dict) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return PLACEHOLDER_RE.sub("", out)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("_")
    return cleaned or "report"


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="校验并渲染游戏评测 / 攻略 HTML")
    parser.add_argument("data", help="report-data.json 路径")
    parser.add_argument("--out", default=None, help="输出目录，默认与数据文件同目录")
    parser.add_argument("--assets", default=None, help="assets 目录，默认脚本上级的 assets/")
    parser.add_argument("--check", action="store_true", help="只校验不渲染")
    parser.add_argument(
        "--image-mode",
        choices=["copy", "inline", "link"],
        default="copy",
        help="图片处理方式：copy 复制到 out/assets（默认）；inline 转 base64 内嵌成单文件；link 引用原路径",
    )
    args = parser.parse_args()

    data_path = Path(args.data).expanduser().resolve()
    if not data_path.is_file():
        print(f"[x] 找不到数据文件: {data_path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[x] JSON 解析失败: {exc}", file=sys.stderr)
        return 2

    validator = Validator(data, base_dir=data_path.parent)
    validator.run()

    if validator.warnings:
        print(f"[!] {len(validator.warnings)} 条警告:")
        for w in validator.warnings:
            print(f"    - {w}")

    if validator.errors:
        print(f"\n[x] 校验未通过，共 {len(validator.errors)} 个问题:", file=sys.stderr)
        for e in validator.errors:
            print(f"    - {e}", file=sys.stderr)
        print(
            "\n请修正数据文件后重试。不要手写 HTML 或修改脚本来绕过校验。",
            file=sys.stderr,
        )
        return 1

    ctx = build_context(data)
    if ctx["no_total"]:
        print("[i] 证据基础不足以输出总分，报告将只展示维度分与定级空缺。")
    else:
        print(f"[i] 加权总分 {fmt_score(ctx['total'])} · 定级「{ctx['verdict']}」")

    if args.check:
        print("[v] 校验通过（--check，未渲染）。")
        return 0

    assets_dir = Path(args.assets).resolve() if args.assets else Path(__file__).resolve().parent.parent / "assets"
    out_dir = Path(args.out).expanduser().resolve() if args.out else data_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    css_path = assets_dir / "report.css"
    if not css_path.is_file():
        print(f"[x] 缺少样式文件: {css_path}", file=sys.stderr)
        return 2
    css = css_path.read_text(encoding="utf-8")

    img_cfg = {
        "mode": args.image_mode,
        "base_dir": data_path.parent,
        "out_dir": out_dir,
    }

    base = safe_filename(data["game"]["title"])
    outputs = []

    review_tpl_path = assets_dir / "review-template.html"
    walk_tpl_path = assets_dir / "walkthrough-template.html"
    for path in (review_tpl_path, walk_tpl_path):
        if not path.is_file():
            print(f"[x] 缺少模板: {path}", file=sys.stderr)
            return 2

    review_html = render_review(ctx, review_tpl_path.read_text(encoding="utf-8"), css, img_cfg)
    review_out = out_dir / f"{base}-评测.html"
    review_out.write_text(review_html, encoding="utf-8")
    outputs.append(review_out)

    walk_html = render_walkthrough(ctx, walk_tpl_path.read_text(encoding="utf-8"), css, img_cfg)
    walk_out = out_dir / f"{base}-攻略.html"
    walk_out.write_text(walk_html, encoding="utf-8")
    outputs.append(walk_out)

    if args.image_mode == "copy":
        img_dir = out_dir / "assets"
        if img_dir.is_dir():
            print(f"[i] 图片已复制到 {img_dir}，分享时请连同该目录一起打包。")

    unv_count = len(as_list(data["walkthrough"].get("unverified")))
    print(f"[v] 已生成 {len(outputs)} 份报告:")
    for path in outputs:
        print(f"    {path}")
    print(f"[i] 待验证条目 {unv_count} 条，已在攻略末尾列明验证方法。")
    if validator.missing_images:
        print(f"[!] 待补图 {len(validator.missing_images)} 处，报告中已渲染为占位框（附所需图说明）：")
        for item in validator.missing_images:
            print(f"    - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
