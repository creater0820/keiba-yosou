"""v1.9.1 で多段化した脚質判定のテスト。

【設計確認事項】
- Tier 1a(corner_1 ≥ 3)は v1.9.0 までと完全同一挙動でなければならない
  (既存テスト 152 件はこのケースを暗黙的に担保している)
- Tier 1b/1c/2/4/5 は v1.9.1 新規。境界値とフォールバック順を確認する
- 後方互換 wrapper `determine_running_style(past_runs)` は "不明(先行扱い)" を
  返さなくなった(v1.9.1)。返すのは "逃げ"/"先行"/"差し"/"追込" の 4 区分のみ
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.race_history import (  # noqa: E402
    CONFIDENCE_LEVELS,
    determine_running_style,
    determine_running_style_with_confidence,
)


def _run(c1=None, c2=None, c3=None, c4=None):
    """テスト用に corner_1〜4 のみ詰めた past_run dict を作る。"""
    return {
        "corner_1": c1, "corner_2": c2, "corner_3": c3, "corner_4": c4,
    }


# ==================================================================
# Tier 1a: corner_1 ≥ 3 走 → 既存ロジック完全不変(high)
# ==================================================================
def test_tier1a_corner1_nigeru():
    """corner_1 平均 ≤ 3 → 逃げ、high。"""
    runs = [_run(c1=1), _run(c1=2), _run(c1=2)]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "逃げ"
    assert conf == "high"


def test_tier1a_corner1_senko_boundary():
    """corner_1 平均 = 4.0(境界、4-6 = 先行)。"""
    runs = [_run(c1=3), _run(c1=4), _run(c1=5)]  # avg=4.0
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "先行"
    assert conf == "high"


def test_tier1a_corner1_sashi():
    """corner_1 平均 9 → 差し、high。"""
    runs = [_run(c1=8), _run(c1=10), _run(c1=9)]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "差し"
    assert conf == "high"


def test_tier1a_corner1_oikomi_boundary():
    """corner_1 平均 = 10.33(境界、>10 = 追込)。"""
    runs = [_run(c1=10), _run(c1=11), _run(c1=10)]  # avg≈10.33
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "追込"
    assert conf == "high"


# ==================================================================
# Tier 1b: corner_1 不足 + corner_3 ≥ 3 走(短距離馬の主救済経路)
# ==================================================================
def test_tier1b_short_distance_with_corner3_only():
    """corner_1 が全て None(短距離戦の JRA 仕様)、corner_3 ≥ 3 走 → high。

    実機 DC260509 で観測された 174 頭の主流パターン。
    """
    runs = [
        _run(c1=None, c3=4, c4=3),
        _run(c1=None, c3=5, c4=5),
        _run(c1=None, c3=6, c4=4),
    ]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "先行"   # corner_3 avg=5.0
    assert conf == "high"


def test_tier1b_corner1_partial_falls_to_corner3():
    """corner_1 が 2 走しか取れず、corner_3 で 3 走以上 → Tier 1b 採用。"""
    runs = [
        _run(c1=2, c3=3),
        _run(c1=3, c3=4),
        _run(c1=None, c3=5),  # c1 欠損だが c3 あり
    ]
    style, conf = determine_running_style_with_confidence(runs)
    # corner_1 は 2 件しか有効値がないので Tier 1a 不採用 → Tier 1b で corner_3
    assert style == "先行"   # corner_3 avg=4.0 → 先行
    assert conf == "high"


# ==================================================================
# Tier 1c: corner_4 のみ ≥ 3 走(medium、ゴール前で順位収束気味)
# ==================================================================
def test_tier1c_corner4_only_medium():
    """corner_4 のみで判定可能 → medium に格下げ。"""
    runs = [
        _run(c4=2),
        _run(c4=3),
        _run(c4=2),
    ]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "逃げ"   # corner_4 avg≈2.33
    assert conf == "medium"


# ==================================================================
# Tier 2: 1-2 走の少サンプル + 利用可能 corner で暫定判定(medium)
# ==================================================================
def test_tier2_only_one_run_with_corner1():
    """過去走 1 走しかない + corner_1 ≥ 1 → medium。"""
    runs = [_run(c1=5)]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "先行"
    assert conf == "medium"


def test_tier2_two_runs_corner3_fallback():
    """過去走 2 走、corner_1 全欠損、corner_3 で暫定 → medium。"""
    runs = [_run(c3=7), _run(c3=8)]
    style, conf = determine_running_style_with_confidence(runs)
    assert style == "差し"   # corner_3 avg=7.5
    assert conf == "medium"


# ==================================================================
# Tier 4: 過去走 0 走 + distance あり → 距離別 default
# ==================================================================
def test_tier4_no_runs_short_distance_default_senko():
    """過去走 0 + 距離 1200m → 先行(default)。新馬の主流ケース。"""
    style, conf = determine_running_style_with_confidence([], distance=1200)
    assert style == "先行"
    assert conf == "default"


def test_tier4_no_runs_long_distance_default_sashi():
    """過去走 0 + 距離 2000m → 差し(default)。"""
    style, conf = determine_running_style_with_confidence([], distance=2000)
    assert style == "差し"
    assert conf == "default"


def test_tier4_no_runs_mile_default_sashi():
    """過去走 0 + 距離 1600m(マイル)→ 差し(>1400m 区分)。"""
    style, conf = determine_running_style_with_confidence([], distance=1600)
    assert style == "差し"
    assert conf == "default"


def test_tier4_short_boundary_1400m():
    """1400m は短距離区分の境界(≤ 1400 = 先行)。"""
    style, _ = determine_running_style_with_confidence([], distance=1400)
    assert style == "先行"
    style, _ = determine_running_style_with_confidence([], distance=1401)
    assert style == "差し"


# ==================================================================
# Tier 5: 全部 None → 絶対 default = 差し
# ==================================================================
def test_tier5_no_runs_no_distance():
    """過去走 0 + distance も None → 差し(default)。"""
    style, conf = determine_running_style_with_confidence([], distance=None)
    assert style == "差し"
    assert conf == "default"


def test_tier5_runs_all_corners_none():
    """過去走はあるが corner_1〜4 全部 None + distance None → 差し default。"""
    runs = [_run(), _run(), _run()]
    style, conf = determine_running_style_with_confidence(runs, distance=None)
    assert style == "差し"
    assert conf == "default"


# ==================================================================
# 後方互換 wrapper: determine_running_style(past_runs) -> str
# ==================================================================
def test_backward_compat_wrapper_returns_only_style():
    """旧 API は str のみ返す(タプルではない)。"""
    runs = [_run(c1=1), _run(c1=2), _run(c1=2)]
    result = determine_running_style(runs)
    assert isinstance(result, str)
    assert result == "逃げ"


def test_backward_compat_wrapper_no_unknown_returned():
    """v1.9.1 以降、wrapper は「不明(先行扱い)」を返さない。

    過去走 0 走 + distance 不明でも安全策で「差し」を返す。
    """
    assert determine_running_style([]) == "差し"
    assert determine_running_style([None, None]) == "差し"


def test_backward_compat_wrapper_matches_tier1a_exactly():
    """Tier 1a 該当ケースで wrapper と v1.9.0 までの仕様が一致(regression)。

    既存テスト 152 件はこのケースを暗黙的に担保しているが、ここでも明示。
    """
    cases = [
        ([_run(c1=1), _run(c1=2), _run(c1=3)], "逃げ"),
        ([_run(c1=4), _run(c1=5), _run(c1=6)], "先行"),
        ([_run(c1=7), _run(c1=9), _run(c1=10)], "差し"),
        ([_run(c1=11), _run(c1=12), _run(c1=14)], "追込"),
    ]
    for runs, expected in cases:
        assert determine_running_style(runs) == expected, f"runs={runs}"


def test_confidence_levels_constant_exposed():
    """CONFIDENCE_LEVELS 定数が公開されていて全レベルを含む。"""
    assert "high" in CONFIDENCE_LEVELS
    assert "medium" in CONFIDENCE_LEVELS
    assert "default" in CONFIDENCE_LEVELS


# ==================================================================
# corner 平均で NaN を持つ run が混じっても skip される(健全性)
# ==================================================================
def test_nan_corner_values_are_skipped():
    """pandas NaN や None が混じっても valid 値だけで平均計算される。"""
    import math
    runs = [
        _run(c1=2),
        _run(c1=float("nan")),
        _run(c1=4),
        _run(c1=3),
    ]
    style, conf = determine_running_style_with_confidence(runs)
    # 有効 3 走の平均 = (2+4+3)/3 = 3.0 → 逃げ(≤ 3 の境界、逃げ採用)
    assert style == "逃げ"
    assert conf == "high"
    assert not math.isnan(3.0)  # sanity


def test_none_runs_are_skipped():
    """past_runs に None が混じってもクラッシュせず有効値だけで判定する。"""
    runs = [None, _run(c1=2), None, _run(c1=3), _run(c1=4)]
    style, conf = determine_running_style_with_confidence(runs)
    # 有効 3 走 (2,3,4) の平均 = 3.0、≤ 3 の境界で「逃げ」
    assert style == "逃げ"
    assert conf == "high"
