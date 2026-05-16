"""utils/course_bias_rules.py(v1.9.0 Phase 1)のテスト。

G1〜G12(G11 欠番)の 11 ルールについて、該当 / 非該当 / 排他 を検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.course_bias_rules import (
    ALL_G_RULES, G_FRAME_RULES, G_STYLE_RULES,
    COURSE_BIAS_SCHEMA_VERSION,
    evaluate_course_bias,
    G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G12,
)


# ==================================================================
# 構造整合性
# ==================================================================
def test_g_rules_count():
    """Phase 1 で 11 ルール(G11 は内外判別不能で延期)。"""
    assert len(G_FRAME_RULES) == 6  # G1〜G6
    assert len(G_STYLE_RULES) == 5  # G7, G8, G9, G10, G12
    assert len(ALL_G_RULES) == 11


def test_schema_version_set():
    """cache 無効化用の schema version が空でない。"""
    assert COURSE_BIAS_SCHEMA_VERSION
    assert "11rules" in COURSE_BIAS_SCHEMA_VERSION


def test_g11_absent():
    """G11(新潟芝外回り 差し)は内外判別不能のため未収録。"""
    rule_ids = {r.rule_id for r in ALL_G_RULES}
    assert "G11" not in rule_ids


def test_g_rates_in_phase1_range():
    """Phase 1 の配点は +5〜+12 の範囲、減点なし。"""
    for r in ALL_G_RULES:
        assert 5 <= r.rate <= 12, f"{r.rule_id} rate={r.rate} 範囲外"


# ==================================================================
# G-Frame 個別ルールの該当 / 非該当
# ==================================================================
def test_G1_tokyo_dirt_outer_frame_hits():
    """東京ダ 1400m + 6 枠 → G1 該当。"""
    m = evaluate_course_bias("東京", "ダ", 1400, frame=6, style="差し")
    assert any(r.rule_id == "G1" for r in m)


def test_G1_inner_frame_misses():
    """東京ダ 1400m + 3 枠 → G1 非該当。"""
    m = evaluate_course_bias("東京", "ダ", 1400, frame=3, style="差し")
    assert not any(r.rule_id == "G1" for r in m)


def test_G1_wrong_surface_misses():
    """東京芝 1400m + 8 枠 → G1 非該当(芝なので別ルール)。"""
    m = evaluate_course_bias("東京", "芝", 1400, frame=8, style="差し")
    assert not any(r.rule_id == "G1" for r in m)


def test_G4_niigata_short_outer_hits_with_higher_rate():
    """新潟芝 1000m + 8 枠 → G4 該当、rate=12(最高値)。"""
    m = evaluate_course_bias("新潟", "芝", 1000, frame=8, style="先行")
    hit = next((r for r in m if r.rule_id == "G4"), None)
    assert hit is not None
    assert hit.rate == 12


def test_G5_fukushima_inner_frame_hits():
    """福島芝 1200m + 1 枠 → G5 該当。"""
    m = evaluate_course_bias("福島", "芝", 1200, frame=1, style="逃げ")
    assert any(r.rule_id == "G5" for r in m)


def test_G6_kokura_outer_frame_hits_with_lower_rate():
    """小倉芝 1200m + 7 枠 → G6 該当、rate=5(最低値、保守的設定)。"""
    m = evaluate_course_bias("小倉", "芝", 1200, frame=7, style="差し")
    hit = next((r for r in m if r.rule_id == "G6"), None)
    assert hit is not None
    assert hit.rate == 5


# ==================================================================
# G-Style 個別ルール
# ==================================================================
def test_G7_hakodate_short_nigeru_hits_with_higher_rate():
    """函館芝 1200m + 逃げ → G7 該当、rate=10。"""
    m = evaluate_course_bias("函館", "芝", 1200, frame=4, style="逃げ")
    hit = next((r for r in m if r.rule_id == "G7"), None)
    assert hit is not None
    assert hit.rate == 10


def test_G7_sashi_misses():
    """函館芝 1200m + 差し → G7 非該当(脚質が違う)。"""
    m = evaluate_course_bias("函館", "芝", 1200, frame=4, style="差し")
    assert not any(r.rule_id == "G7" for r in m)


def test_G8_nakayama_dirt_1800_senko_hits():
    """中山ダ 1800m + 先行 → G8 該当。"""
    m = evaluate_course_bias("中山", "ダ", 1800, frame=5, style="先行")
    assert any(r.rule_id == "G8" for r in m)


def test_G9_kyoto_dirt_1800_nigeru_hits():
    """京都ダ 1800m + 逃げ → G9 該当。"""
    m = evaluate_course_bias("京都", "ダ", 1800, frame=3, style="逃げ")
    assert any(r.rule_id == "G9" for r in m)


def test_G10_hanshin_dirt_1800_senko_hits():
    """阪神ダ 1800m + 先行 → G10 該当。"""
    m = evaluate_course_bias("阪神", "ダ", 1800, frame=6, style="先行")
    assert any(r.rule_id == "G10" for r in m)


def test_G12_fukushima_short_nigeru_hits():
    """福島芝 1200m + 逃げ → G12 該当。"""
    m = evaluate_course_bias("福島", "芝", 1200, frame=4, style="逃げ")
    assert any(r.rule_id == "G12" for r in m)


# ==================================================================
# 排他: G-Frame 同士、G-Style 同士は同時に複数発火しない
# ==================================================================
def test_g_frame_exclusion_within_category():
    """各馬で G-Frame は最大 1 つ(コース × 枠範囲が一意なので原理的に排他)。
    複数該当しうるシナリオ(全ルール走査)でも 1 つだけ返ること。"""
    # 東京ダ 1400m + 8 枠 + 差し
    m = evaluate_course_bias("東京", "ダ", 1400, frame=8, style="差し")
    frame_hits = [r for r in m if r.category == "G-Frame"]
    assert len(frame_hits) <= 1


def test_g_style_exclusion_within_category():
    """G-Style も同様に同時に複数発火しない。"""
    m = evaluate_course_bias("福島", "芝", 1200, frame=4, style="逃げ")
    style_hits = [r for r in m if r.category == "G-Style"]
    assert len(style_hits) <= 1


# ==================================================================
# 独立: G-Frame と G-Style は両方発火可能
# ==================================================================
def test_g_frame_and_g_style_both_can_fire():
    """福島芝 1200m + 1 枠 + 逃げ → G5(枠)と G12(脚質)が両方発火。"""
    m = evaluate_course_bias("福島", "芝", 1200, frame=1, style="逃げ")
    rule_ids = {r.rule_id for r in m}
    assert "G5" in rule_ids
    assert "G12" in rule_ids
    assert len(m) == 2


# ==================================================================
# 無関係なコースで一切発火しない
# ==================================================================
def test_no_match_for_unrelated_course():
    """中京芝 2000m + 4 枠 + 差し → どの G ルールにも該当しない
    (Phase 1 では中京は未収録)。"""
    m = evaluate_course_bias("中京", "芝", 2000, frame=4, style="差し")
    assert m == []


def test_safe_for_unknown_inputs():
    """空文字列や 0 distance でも例外を出さず空リストを返す。"""
    assert evaluate_course_bias("", "", 0, frame=0, style="") == []
    assert evaluate_course_bias("不明", "芝", 1200, frame=4, style="逃げ") == []
