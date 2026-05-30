"""utils/ev_engine.py の単体テスト(v1.13.0 Phase 2)。

公正オッズ / EV / お買い得フラグ / Kelly の数値的性質とエッジケースを検証する。
"""

from __future__ import annotations

from dataclasses import dataclass

import math
import pytest

from utils.ev_engine import (
    EV_SCHEMA_VERSION,
    EV_THRESHOLD_HIGH,
    EV_THRESHOLD_MID,
    MIN_ODDS_FOR_RECOMMENDATION,
    REC_HIGH,
    REC_LOW,
    REC_MID,
    REC_NONE,
    HorseEV,
    compute_horse_ev,
    compute_race_evs,
    kelly_fraction,
)


@dataclass(frozen=True)
class FakeProb:
    """compute_race_evs が読む HorseProbability 互換の最小型。"""
    horse_id: str
    horse_number: int
    win_prob: float
    place_prob: float
    is_excluded: bool = False


# =====================================================================
# 公正オッズ
# =====================================================================

def test_fair_odds_tan_from_win_prob():
    """win_prob 0.5 → 公正単勝オッズ 2.0。"""
    ev = compute_horse_ev(win_prob=0.5, place_prob=0.9)
    assert abs(ev.fair_odds_tan - 2.0) < 1e-9


def test_fair_odds_fuku_from_place_prob():
    """place_prob 0.25 → 公正複勝オッズ 4.0。"""
    ev = compute_horse_ev(win_prob=0.1, place_prob=0.25)
    assert abs(ev.fair_odds_fuku - 4.0) < 1e-9


def test_fair_odds_none_when_zero_prob():
    """win_prob 0(除外馬)→ 公正オッズ None。"""
    ev = compute_horse_ev(win_prob=0.0, place_prob=0.0)
    assert ev.fair_odds_tan is None
    assert ev.fair_odds_fuku is None


# =====================================================================
# EV 計算
# =====================================================================

def test_ev_tan_known_value():
    """win_prob 0.4, odds 3.0 → EV = 0.4*3.0 - 1 = 0.20。"""
    ev = compute_horse_ev(win_prob=0.4, place_prob=0.7, market_odds_tan=3.0)
    assert abs(ev.ev_tan - 0.20) < 1e-9


def test_ev_fuku_known_value():
    """place_prob 0.6, fuku odds 2.0 → EV = 0.6*2.0 - 1 = 0.20。"""
    ev = compute_horse_ev(win_prob=0.3, place_prob=0.6, market_odds_fuku=2.0)
    assert abs(ev.ev_fuku - 0.20) < 1e-9


def test_ev_negative_when_odds_too_low():
    """win_prob 0.5, odds 1.5 → EV = -0.25(マイナス)。"""
    ev = compute_horse_ev(win_prob=0.5, place_prob=0.9, market_odds_tan=1.5)
    assert ev.ev_tan < 0


def test_ev_none_without_market_odds():
    """マーケットオッズ未入力 → EV は None(公正オッズのみ)。"""
    ev = compute_horse_ev(win_prob=0.5, place_prob=0.9)
    assert ev.ev_tan is None
    assert ev.ev_fuku is None
    assert ev.recommendation == REC_NONE


def test_ev_fair_break_even_is_zero():
    """マーケットオッズ = 公正オッズ なら EV ≈ 0。"""
    ev = compute_horse_ev(win_prob=0.25, place_prob=0.6, market_odds_tan=4.0)
    assert abs(ev.ev_tan) < 1e-9


# =====================================================================
# お買い得フラグ(recommendation)
# =====================================================================

def test_rec_high():
    """EV +25%(≥20%)+ オッズ ≥ 2.0 → 高期待値。"""
    ev = compute_horse_ev(win_prob=0.25, place_prob=0.6, market_odds_tan=5.0)
    assert abs(ev.ev_tan - 0.25) < 1e-9
    assert ev.recommendation == REC_HIGH


def test_rec_mid():
    """EV +12%(10〜20%)→ お買い得。"""
    ev = compute_horse_ev(win_prob=0.28, place_prob=0.6, market_odds_tan=4.0)
    assert ev.recommendation == REC_MID


def test_rec_low():
    """EV +4%(0〜10%)→ プラス期待値。"""
    ev = compute_horse_ev(win_prob=0.26, place_prob=0.6, market_odds_tan=4.0)
    assert ev.recommendation == REC_LOW


def test_rec_none_when_negative_ev():
    """EV マイナス → フラグなし。"""
    ev = compute_horse_ev(win_prob=0.2, place_prob=0.6, market_odds_tan=3.0)
    assert ev.ev_tan < 0
    assert ev.recommendation == REC_NONE


def test_rec_none_when_odds_below_min():
    """EV プラスでもオッズ < 2.0 ならフラグなし(低オッズ除外)。"""
    # win_prob 0.6, odds 1.8 → EV = 0.08(プラス)だが odds < 2.0
    ev = compute_horse_ev(win_prob=0.6, place_prob=0.9, market_odds_tan=1.8)
    assert ev.ev_tan > 0
    assert ev.recommendation == REC_NONE


def test_rec_threshold_override():
    """閾値を引数で上書きできる(サイドバースライダー相当)。"""
    # EV = 0.05。既定では LOW だが threshold_low=0.10 にすると NONE
    ev = compute_horse_ev(
        win_prob=0.21, place_prob=0.6, market_odds_tan=5.0,
        threshold_low=0.10, threshold_mid=0.20, threshold_high=0.40,
    )
    assert abs(ev.ev_tan - 0.05) < 1e-9
    assert ev.recommendation == REC_NONE


# =====================================================================
# compute_race_evs(レース単位)
# =====================================================================

def test_race_evs_fair_only_without_odds():
    """market_odds_dict なし → 全馬 公正オッズのみ、EV は None。"""
    probs = [
        FakeProb("a", 1, 0.5, 0.9),
        FakeProb("b", 2, 0.25, 0.6),
    ]
    evs = compute_race_evs(probs)
    assert len(evs) == 2
    assert all(e.ev_tan is None for e in evs)
    assert abs(evs[0].fair_odds_tan - 2.0) < 1e-9


def test_race_evs_with_market_odds():
    """一部の馬だけオッズ入力 → その馬だけ EV が出る。"""
    probs = [
        FakeProb("a", 1, 0.4, 0.8),
        FakeProb("b", 2, 0.2, 0.5),
    ]
    evs = {e.horse_number: e for e in compute_race_evs(
        probs, market_odds_dict={1: {"tan": 3.0}},
    )}
    assert abs(evs[1].ev_tan - 0.20) < 1e-9
    assert evs[2].ev_tan is None


def test_race_evs_preserves_order():
    """戻り値は入力順を維持。"""
    probs = [FakeProb(f"h{i}", i, 0.1, 0.3) for i in (3, 1, 2)]
    evs = compute_race_evs(probs)
    assert [e.horse_number for e in evs] == [3, 1, 2]


def test_race_evs_excluded_horse_fair_none():
    """除外馬(win_prob 0)は公正オッズ None・EV None。"""
    probs = [FakeProb("a", 1, 0.0, 0.0, is_excluded=True),
             FakeProb("b", 2, 0.5, 0.9)]
    evs = {e.horse_number: e for e in compute_race_evs(
        probs, market_odds_dict={1: {"tan": 5.0}},
    )}
    assert evs[1].fair_odds_tan is None
    assert evs[1].ev_tan is None


# =====================================================================
# Kelly 基準
# =====================================================================

def test_kelly_known_value():
    """win_prob 0.5, odds 3.0 → b=2, f=(2*0.5-0.5)/2=0.25。"""
    assert abs(kelly_fraction(0.5, 3.0) - 0.25) < 1e-9


def test_kelly_zero_when_no_edge():
    """期待値ゼロ(公正オッズ)なら Kelly = 0。"""
    # win_prob 0.25, odds 4.0 → EV 0 → f = 0
    assert abs(kelly_fraction(0.25, 4.0) - 0.0) < 1e-9


def test_kelly_zero_when_odds_le_one():
    """オッズ ≤ 1.0 は 0(賭けない)。"""
    assert kelly_fraction(0.9, 1.0) == 0.0
    assert kelly_fraction(0.9, 0.5) == 0.0


def test_kelly_clipped_0_1():
    """Kelly は [0, 1] にクリップされる。"""
    f = kelly_fraction(0.99, 100.0)
    assert 0.0 <= f <= 1.0


def test_kelly_negative_edge_returns_zero():
    """負のエッジ(EV<0)は 0。"""
    # win_prob 0.1, odds 3.0 → EV = -0.7 → f < 0 → clip 0
    assert kelly_fraction(0.1, 3.0) == 0.0


# =====================================================================
# 定数・スキーマ
# =====================================================================

def test_schema_version_and_constants():
    assert EV_SCHEMA_VERSION
    assert EV_THRESHOLD_HIGH > EV_THRESHOLD_MID
    assert MIN_ODDS_FOR_RECOMMENDATION >= 1.0
