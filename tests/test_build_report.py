"""build_report.py 的校验逻辑测试。

重点不是「正常数据能不能渲染」，而是**故意喂坏数据，确认它真的会拦**。
这个 skill 的全部价值建立在「不合规的产物根本生不出来」之上，
所以失败路径的覆盖度就是它的可信度上限。

跑法:
    python -m pytest -q
    python -m pytest -v            # 看每条用例名
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_report.py"
ASSETS = ROOT / "assets"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = _load_module()


# ---------------------------------------------------------------- fixtures

def ev(tier="verified_play", **kw):
    """构造一条证据。默认是最强的 E1。"""
    base = {"tier": tier, "note": "说明"}
    base.update(kw)
    return base


def htv(where="某关", what="做某事", watch="看某现象"):
    return {"where": where, "what": what, "watch": watch}


def dim(dim_id, score=8, **override):
    d = {
        "id": dim_id,
        "name": f"维度 {dim_id}",
        "weight": br.WEIGHTS[dim_id],
        "score": score,
        "plus": ["可观察的加分现象"],
        "minus": ["可观察的扣分现象"],
        "evidence": [ev()],
    }
    d.update(override)
    return d


def entry_a(title="第一章"):
    return {
        "title": title,
        "goal": "抵达下一个区域",
        "evidence": [ev()],
        "route": ["枢纽 → 第一生物群系"],
    }


def entry_b(title="卡关点"):
    return {"title": title, "tolerance": "约 3 帧", "evidence": [ev()]}


def entry_c(title="某 BOSS"):
    return {
        "title": title,
        "evidence": [ev()],
        "phases": [{"tell": "抬手动作", "punish": "绕背输出", "risk": "一次失误掉半血"}],
    }


def entry_d(title="隐藏要素"):
    return {"title": title, "evidence": [ev()]}


def valid_data(evidence_base="cleared_15h_plus", scores=None, **override):
    """一份能通过全部校验的最小数据。"""
    scores = scores or {}
    data = {
        "schema_version": 1,
        "game": {
            "title": "测试游戏",
            "platforms": ["PC"],
            "version": "1.0.0",
            "version_confirmed": True,
            "review_date": "2026-09-16",
            "evidence_base": evidence_base,
            "disclosure": "无外部利益关系",
            "materials": [{"path": "x.sav", "content": "存档", "progress": "通关"}],
        },
        "review": {
            "summary": "一句话总结。",
            "dimensions": [dim(d, scores.get(d, 8)) for d in br.WEIGHTS],
            "bias_audit": [
                {"risk": f"风险{i}", "finding": "发现", "action": "处置"} for i in range(5)
            ],
            "pros": ["优点一", "优点二", "优点三"],
            "cons": ["缺点一", "缺点二", "缺点三"],
            "sources": [{"title": "来源", "url": "https://example.com/a"}],
        },
        "walkthrough": {
            "scope": "覆盖主线四层。",
            "layers": {
                "A_main_route": [entry_a()],
                "B_blockers": [entry_b()],
                "C_bosses": [entry_c()],
                "D_collectibles": [entry_d()],
            },
            "unverified": [],
        },
    }
    data.update(override)
    return data


def validate(data, base_dir=None):
    v = br.Validator(data, base_dir=base_dir)
    v.run()
    return v


def joined(items):
    return " || ".join(items)


# ---------------------------------------------------------------- 基线自检

def test_baseline_fixture_is_actually_valid():
    """如果这条挂了，说明后面的用例全部失去意义——先修 fixture。"""
    v = validate(valid_data())
    assert v.errors == [], joined(v.errors)


# ---------------------------------------------------------------- 证据分级

class TestEvidenceTier:
    @pytest.mark.parametrize(
        "tier", ["verified_play", "user_material", "sourced", "inferred", "unknown"]
    )
    def test_each_tier_accepted_when_complete(self, tier):
        e = ev(tier)
        if tier == "sourced":
            e["source_url"] = "https://example.com"
            e["source_title"] = "出处"
        if tier == "user_material":
            e["material_ref"] = "capture/01.png"
        if tier == "inferred":
            e["basis"] = "由 A 机制推得 B"
            e["how_to_verify"] = htv()
        if tier == "unknown":
            e["how_to_verify"] = htv()
        v = br.Validator({"game": {}, "review": {}, "walkthrough": {}})
        v.check_evidence([e], "probe")
        assert v.errors == [], joined(v.errors)

    def test_illegal_tier_rejected(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("verified_by_vibes")], "probe")
        assert any("tier 非法" in e for e in v2.errors)

    def test_empty_evidence_rejected(self):
        v2 = br.Validator({})
        v2.check_evidence([], "probe")
        assert any("缺少 evidence" in e for e in v2.errors)

    def test_evidence_not_object_rejected(self):
        v2 = br.Validator({})
        v2.check_evidence(["verified_play"], "probe")
        assert any("不是对象" in e for e in v2.errors)

    @pytest.mark.parametrize("bad", [[], [{"tier": "verified_play"}]])
    def test_sourced_requires_url_or_two_sources(self, bad):
        """E3 要么给一手链接，要么给两条独立来源。单条二手来源不算。"""
        v2 = br.Validator({})
        v2.check_evidence([ev("sourced", source=bad)], "probe")
        assert any("tier=sourced" in e for e in v2.errors)

    def test_sourced_with_single_url_passes(self):
        v2 = br.Validator({})
        v2.check_evidence(
            [ev("sourced", source_url="https://example.com", source_title="出处")], "probe"
        )
        assert v2.errors == []

    def test_sourced_with_two_independent_sources_passes(self):
        v2 = br.Validator({})
        v2.check_evidence(
            [ev("sourced", source=[{"url": "https://a.com"}, {"url": "https://b.com"}])],
            "probe",
        )
        assert v2.errors == []

    def test_sourced_with_one_source_and_no_url_rejected(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("sourced", source=[{"url": "https://a.com"}])], "probe")
        assert any("tier=sourced" in e for e in v2.errors)

    def test_user_material_requires_material_ref(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("user_material")], "probe")
        assert any("material_ref" in e for e in v2.errors)

        v3 = br.Validator({})
        v3.check_evidence([ev("user_material", material_ref="cap/01.png")], "probe")
        assert v3.errors == []

    def test_inferred_requires_basis(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("inferred", how_to_verify=htv())], "probe")
        assert any("basis" in e for e in v2.errors)

    @pytest.mark.parametrize("tier", ["inferred", "unknown"])
    def test_inferred_and_unknown_require_how_to_verify(self, tier):
        """这是「不写迷宫式攻略」的强制点：说不出怎么验，就不许写进正文。"""
        v2 = br.Validator({})
        e = ev(tier)
        if tier == "inferred":
            e["basis"] = "推导链"
        v2.check_evidence([e], "probe")
        assert any("how_to_verify" in e for e in v2.errors)

    @pytest.mark.parametrize("missing", ["where", "what", "watch"])
    def test_how_to_verify_needs_all_three_fields(self, missing):
        v2 = br.Validator({})
        h = htv()
        h[missing] = "   "
        e = ev("unknown", how_to_verify=h)
        v2.check_evidence([e], "probe")
        assert any("how_to_verify" in x for x in v2.errors)

    def test_unknown_tier_emits_warning(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("unknown", how_to_verify=htv())], "probe")
        assert any("未验证证据" in w for w in v2.warnings)

    def test_sourced_without_title_warns_but_passes(self):
        v2 = br.Validator({})
        v2.check_evidence([ev("sourced", source_url="https://example.com")], "probe")
        assert v2.errors == []
        assert any("source_title" in w for w in v2.warnings)


# ---------------------------------------------------------------- 权重与维度

class TestWeights:
    def test_weight_must_match_rubric_fixed_value(self):
        data = valid_data()
        data["review"]["dimensions"][0]["weight"] = 0.99
        v = validate(data)
        assert any("weight 与 rubric 固定值不一致" in e for e in v.errors)

    def test_weight_sum_must_equal_one(self):
        """独立校验「权重和」这一层。

        权重是全局固定的，所以只要 7 个维度都通过了「与 rubric 定值一致」的检查，
        和就必然等于 1——「和」这一层是纵深防御，只在维度数或 id 出问题时兜底。
        变异测试发现：破坏这层检查曾不导致任何用例失败，等于它是装饰。
        这里改成断言它专用的错误文案，逼它必须真的报错。
        """
        data = valid_data()
        data["review"]["dimensions"][0]["weight"] = 0.12  # core_loop 应为 0.20，和降到 0.92
        v = validate(data)
        assert any("权重和为" in e for e in v.errors), joined(v.errors)

    def test_missing_dimension_rejected(self):
        data = valid_data()
        data["review"]["dimensions"].pop()
        v = validate(data)
        assert any("需要 7 个维度" in e or "缺少维度" in e for e in v.errors)

    def test_duplicate_dimension_id_rejected(self):
        data = valid_data()
        data["review"]["dimensions"][1]["id"] = "core_loop"
        v = validate(data)
        assert any("id 重复" in e for e in v.errors)

    def test_illegal_dimension_id_rejected(self):
        data = valid_data()
        data["review"]["dimensions"][0]["id"] = "graphics"
        v = validate(data)
        assert any("id 非法" in e for e in v.errors)

    def test_weights_are_globally_fixed(self):
        """权重不得按品类调整——这是跨游戏可比性的前提。"""
        data = valid_data()
        data["review"]["dimensions"][2]["weight"] = 0.16
        v = validate(data)
        assert any("不得按品类调整" in e for e in v.errors)

    @pytest.mark.parametrize("bad_score", [0, 11, -1, 7.5, "8", None, True])
    def test_score_must_be_int_1_to_10(self, bad_score):
        data = valid_data()
        data["review"]["dimensions"][0]["score"] = bad_score
        v = validate(data)
        assert any("score 必须是 1-10 的整数" in e for e in v.errors)

    @pytest.mark.parametrize("field", ["plus", "minus"])
    def test_dimension_needs_both_anchors(self, field):
        """没有锚点的分数等于没打。"""
        data = valid_data()
        data["review"]["dimensions"][0][field] = []
        v = validate(data)
        assert any(field in e for e in v.errors)

    def test_dimension_needs_evidence(self):
        data = valid_data()
        data["review"]["dimensions"][0]["evidence"] = []
        v = validate(data)
        assert any("缺少 evidence" in e for e in v.errors)


# ---------------------------------------------------------------- 分数封顶

class TestScoreCap:
    def _total(self, evidence_base, score):
        v = validate(valid_data(evidence_base, scores={d: score for d in br.WEIGHTS}))
        assert v.errors == [], joined(v.errors)
        return br.build_context(valid_data(evidence_base, {d: score for d in br.WEIGHTS}))

    @pytest.mark.parametrize(
        "evidence_base,expected_cap",
        [
            ("cleared_15h_plus", 10.0),
            ("cleared_time_unverified", 8.5),
            ("cleared_under_15h", 8.5),
            ("core_loop_entered", 7.5),
            ("research_only", 6.5),
        ],
    )
    def test_all_tens_are_capped_at_evidence_ceiling(self, evidence_base, expected_cap):
        """满分数据在不同证据基础上必须被截断到对应上限。"""
        ctx = self._total(evidence_base, 10)
        assert ctx["total"] == expected_cap
        assert ctx["no_total"] is False

    def test_early_under_3h_outputs_no_total(self):
        ctx = self._total("early_under_3h", 10)
        assert ctx["no_total"] is True
        assert ctx["verdict"] == "不出定级"

    def test_cap_not_applied_when_total_already_below(self):
        """上限是天花板，不是地板——低分不该被抬上去。"""
        ctx = self._total("cleared_time_unverified", 5)
        assert ctx["total"] == 5.0
        assert not any("上限生效" in w for w in ctx["warnings"])

    def test_cap_emits_explanation_warning(self):
        ctx = self._total("cleared_time_unverified", 10)
        assert any("上限生效" in w for w in ctx["warnings"])

    def test_self_reported_time_cannot_reach_the_10_cap(self):
        """玩家自述时长不算可核证据，不得据此走 10.0 那一档。"""
        ctx = self._total("cleared_time_unverified", 10)
        assert ctx["total"] < 10.0

    def test_illegal_evidence_base_rejected(self):
        v = validate(valid_data(evidence_base="i_beat_it_trust_me"))
        assert any("evidence_base" in e for e in v.errors)

    def test_missing_materials_emits_warning(self):
        data = valid_data()
        data["game"]["materials"] = []
        v = validate(data)
        assert any("materials" in w for w in v.warnings)

    def test_unconfirmed_version_emits_warning(self):
        data = valid_data()
        data["game"]["version_confirmed"] = False
        v = validate(data)
        assert any("版本未确认" in w for w in v.warnings)


# ---------------------------------------------------------------- 定级与显示一致性

class TestVerdict:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (10.0, "标杆级"),
            (9.0, "标杆级"),
            (8.9, "强烈推荐"),
            (8.0, "强烈推荐"),
            (7.9, "推荐"),
            (7.0, "推荐"),
            (6.9, "尚可"),
            (6.0, "尚可"),
            (5.0, "谨慎"),
            (4.9, "不推荐"),
            (0.0, "不推荐"),
        ],
    )
    def test_verdict_thresholds(self, score, expected):
        assert br.verdict_for(score) == expected

    def test_displayed_total_and_verdict_never_contradict(self):
        """回归测试：6.99 必须显示 7.0 且判「推荐」。

        分工如下（权重和为 100）：
            7*20 + 7*18 + 7*15 + 7*14 + 7*12 + 6*11 + 8*10 = 699 → 原始 6.99
        历史上这里是先判级再取整，于是出现「显示 7.0 但定级是尚可」的自相矛盾。
        """
        scores = {
            "core_loop": 7,
            "level_design": 7,
            "feel_feedback": 7,
            "systems_balance": 7,
            "presentation": 7,
            "tech_stability": 6,
            "value_content": 8,
        }
        raw = sum(s * br.WEIGHTS[d] for d, s in scores.items())
        assert round(raw, 2) == 6.99, "用例前提算错了"

        data = valid_data("cleared_15h_plus", scores=scores)
        assert validate(data).errors == []
        ctx = br.build_context(data)

        assert ctx["total"] == 7.0
        assert ctx["verdict"] == br.verdict_for(7.0) == "推荐"
        assert ctx["verdict"] != "尚可", "定级用了未取整的原始分，显示与判级打架"

    @pytest.mark.parametrize("evidence_base", list(br.EVIDENCE_BASE))
    def test_total_never_exceeds_declared_ceiling(self, evidence_base):
        """对每一种证据基础，任何维度的组合都不得突破上限。"""
        cap = br.EVIDENCE_BASE[evidence_base][1]
        if cap is None:
            pytest.skip("该证据基础不出总分")
        for score in (7, 8, 9, 10):
            ctx = br.build_context(
                valid_data(evidence_base, {d: score for d in br.WEIGHTS})
            )
            assert ctx["total"] <= cap + 1e-9


# ---------------------------------------------------------------- A 层禁祈使句

class TestNoUnknownStepsInRoute:
    def test_unverified_step_in_route_rejected(self):
        data = valid_data()
        data["walkthrough"]["layers"]["A_main_route"][0]["route"] = ["先去左边（未验证）"]
        v = validate(data)
        assert any("未验证内容不得混入 A 层" in e for e in v.errors)

    @pytest.mark.parametrize("word", ["待验证", "未验证"])
    def test_both_wording_variants_rejected(self, word):
        data = valid_data()
        data["walkthrough"]["layers"]["A_main_route"][0]["route"] = [f"某步骤（{word}）"]
        assert any("不得混入 A 层" in e for e in validate(data).errors)

    @pytest.mark.parametrize(
        "step", ["先往左走", "然后跳上平台", "向东北移动", "按 E 开门", "攻击弱点"]
    )
    def test_imperative_wording_warns(self, step):
        data = valid_data()
        data["walkthrough"]["layers"]["A_main_route"][0]["route"] = [step]
        v = validate(data)
        assert v.errors == []
        assert any("祈使句走位" in w for w in v.warnings)

    def test_imperative_with_inferred_marker_is_allowed(self):
        data = valid_data()
        data["walkthrough"]["layers"]["A_main_route"][0]["route"] = ["先往左走 [推断]"]
        v = validate(data)
        assert not any("祈使句走位" in w for w in v.warnings)


# ---------------------------------------------------------------- 攻略结构

class TestWalkthroughStructure:
    @pytest.mark.parametrize(
        "key", ["A_main_route", "B_blockers", "C_bosses", "D_collectibles"]
    )
    def test_every_layer_required(self, key):
        data = valid_data()
        data["walkthrough"]["layers"][key] = []
        v = validate(data)
        assert any(key in e for e in v.errors)

    def test_scope_required(self):
        data = valid_data()
        data["walkthrough"]["scope"] = "  "
        assert any("scope" in e for e in validate(data).errors)

    @pytest.mark.parametrize("field", ["tell", "punish", "risk"])
    def test_boss_phase_needs_tell_punish_risk(self, field):
        data = valid_data()
        data["walkthrough"]["layers"]["C_bosses"][0]["phases"][0][field] = ""
        v = validate(data)
        assert any("tell / punish / risk" in e for e in v.errors)

    def test_blocker_entry_needs_tolerance_or_phases(self):
        data = valid_data()
        del data["walkthrough"]["layers"]["B_blockers"][0]["tolerance"]
        v = validate(data)
        assert any("容错窗口" in e for e in v.errors)

    def test_blocker_entry_with_tolerance_passes(self):
        assert validate(valid_data()).errors == []

    def test_unverified_item_needs_full_how_to_verify(self):
        data = valid_data()
        data["walkthrough"]["unverified"] = [
            {"question": "某机制到底怎样", "how_to_verify": {"where": "某关"}}
        ]
        v = validate(data)
        assert any("where / what / watch" in e for e in v.errors)

    def test_well_formed_unverified_item_passes(self):
        data = valid_data()
        data["walkthrough"]["unverified"] = [
            {"question": "某机制到底怎样", "how_to_verify": htv()}
        ]
        assert validate(data).errors == []

    def test_missing_unverified_answer_question_rejected(self):
        data = valid_data()
        data["walkthrough"]["unverified"] = [{"how_to_verify": htv()}]
        assert any("question" in e for e in validate(data).errors)


# ---------------------------------------------------------------- 图片纪律

class TestImageDiscipline:
    def _with_image(self, img):
        data = valid_data()
        data["walkthrough"]["layers"]["A_main_route"][0]["images"] = [img]
        return data

    def test_illegal_kind_rejected(self):
        v = validate(self._with_image({"kind": "ai_generated", "caption": "c"}))
        assert any("kind 非法" in e for e in v.errors)

    def test_diagram_must_declare_is_illustration(self):
        v = validate(
            self._with_image({"kind": "diagram", "caption": "c", "diagram_type": "generic"})
        )
        assert any("is_illustration=true" in e for e in v.errors)

    def test_illustration_cannot_pose_as_gameplay_capture(self):
        """伪造游戏截图是本技能的头号红线。"""
        v = validate(
            self._with_image(
                {
                    "kind": "user_capture",
                    "caption": "c",
                    "path": "nope.png",
                    "is_illustration": True,
                }
            )
        )
        assert any("不能标为" in e for e in v.errors)

    @pytest.mark.parametrize("missing", ["source_url", "credit"])
    def test_official_image_needs_url_and_credit(self, missing):
        img = {
            "kind": "official",
            "caption": "c",
            "source_url": "https://example.com",
            "credit": "Studio",
        }
        img.pop(missing)
        v = validate(self._with_image(img))
        assert any(missing in e for e in v.errors)

    def test_caption_required(self):
        v = validate(self._with_image({"kind": "diagram", "is_illustration": True}))
        assert any("caption" in e for e in v.errors)

    def test_illegal_diagram_type_rejected(self):
        v = validate(
            self._with_image(
                {
                    "kind": "diagram",
                    "is_illustration": True,
                    "caption": "c",
                    "diagram_type": "hologram",
                }
            )
        )
        assert any("diagram_type 非法" in e for e in v.errors)

    def test_real_kind_needs_path(self):
        v = validate(self._with_image({"kind": "user_capture", "caption": "c"}))
        assert any("必须提供 path" in e for e in v.errors)

    def test_missing_image_file_warns_and_is_listed(self):
        v = validate(
            self._with_image({"kind": "user_capture", "caption": "c", "path": "nope.png"})
        )
        assert v.errors == []
        assert any("图片文件不存在" in w for w in v.warnings)
        assert v.missing_images


# ---------------------------------------------------------------- 评测段其他必填

class TestReviewRequired:
    def test_bias_audit_needs_five_items(self):
        data = valid_data()
        data["review"]["bias_audit"] = [{"risk": "r", "finding": "f", "action": "a"}]
        v = validate(data)
        assert any("至少 5 条自查" in e for e in v.errors)

    @pytest.mark.parametrize("field", ["risk", "finding", "action"])
    def test_bias_audit_item_needs_three_fields(self, field):
        data = valid_data()
        data["review"]["bias_audit"][0][field] = ""
        v = validate(data)
        assert any("risk / finding / action" in e for e in v.errors)

    @pytest.mark.parametrize("field,count", [("pros", 2), ("cons", 2)])
    def test_pros_and_cons_need_three(self, field, count):
        data = valid_data()
        data["review"][field] = ["a", "b"][:count]
        assert any(field in e for e in validate(data).errors)

    def test_source_needs_url(self):
        data = valid_data()
        data["review"]["sources"] = [{"title": "只有标题"}]
        assert any("sources" in e for e in validate(data).errors)

    @pytest.mark.parametrize(
        "field", ["title", "platforms", "version", "review_date", "evidence_base", "disclosure"]
    )
    def test_game_required_fields(self, field):
        data = valid_data()
        data["game"][field] = ""
        v = validate(data)
        assert any(field in e for e in v.errors)


# ---------------------------------------------------------------- CLI 端到端

def run_cli(tmp_path, data, *extra):
    path = tmp_path / "report-data.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TestCLI:
    def test_check_passes_and_writes_nothing(self, tmp_path):
        r = run_cli(tmp_path, valid_data(), "--check", "--out", str(tmp_path / "out"))
        assert r.returncode == 0, r.stderr
        assert "校验通过" in r.stdout
        assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.html"))

    def test_invalid_data_exits_nonzero_and_writes_no_html(self, tmp_path):
        """核心保证：校验不通过时非零退出，且**一个 HTML 都不产出**。"""
        data = valid_data()
        data["review"]["dimensions"][0]["score"] = 99
        out = tmp_path / "out"
        r = run_cli(tmp_path, data, "--out", str(out))
        assert r.returncode == 1
        assert "校验未通过" in r.stderr
        assert not list(out.glob("*.html")) if out.exists() else True

    def test_error_message_warns_against_bypass(self, tmp_path):
        data = valid_data()
        data["game"]["evidence_base"] = "trust_me"
        r = run_cli(tmp_path, data, "--check")
        assert r.returncode == 1
        assert "不要手写 HTML" in r.stderr

    def test_missing_file_exit_2(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path / "nope.json"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert r.returncode == 2

    def test_malformed_json_exit_2(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(p), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert r.returncode == 2
        assert "JSON 解析失败" in r.stderr

    def test_render_produces_two_html(self, tmp_path):
        out = tmp_path / "out"
        r = run_cli(tmp_path, valid_data(), "--out", str(out))
        assert r.returncode == 0, r.stderr

        review = out / "测试游戏-评测.html"
        walk = out / "测试游戏-攻略.html"
        assert review.is_file() and walk.is_file()

        # 注意：不能靠「输出里没有 {{」来判断渲染完整——fill() 最后会 sub 掉残留占位符，
        # 那条断言永远为真。要验证的是「该填的东西确实填进去了」。
        rv = review.read_text(encoding="utf-8")
        wk = walk.read_text(encoding="utf-8")

        for text, name in ((rv, "评测"), (wk, "攻略")):
            assert "测试游戏" in text, f"{name}报告里没有游戏名"
            assert "<html" in text.lower()
            assert len(text) > 4000, f"{name}报告过短，模板可能没填上"

        assert 'class="radar"' in rv, "评测报告缺少雷达图"
        assert "覆盖主线四层。" in wk, "攻略报告缺少 scope"
        assert "可观察的加分现象" in rv, "维度锚点没渲染出来"
        assert "维度 core_loop" in rv, "维度卡没渲染出来"

    def test_render_reports_warning_count(self, tmp_path):
        data = valid_data()  # A/C 层无配图 → 应有警告但不是错误
        r = run_cli(tmp_path, data, "--out", str(tmp_path / "out"))
        assert r.returncode == 0
        assert "警告" in r.stdout
        assert "已生成 2 份报告" in r.stdout

    def test_assets_dir_present(self):
        """渲染依赖这三样，缺了 CLI 会 exit 2。"""
        for name in ("report.css", "review-template.html", "walkthrough-template.html"):
            assert (ASSETS / name).is_file(), f"缺少 {name}"
