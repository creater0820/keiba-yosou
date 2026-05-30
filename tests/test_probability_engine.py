"""utils/probability_engine.py の単体テスト(v1.12.0 Phase 1)。

softmax 単勝確率 + Plackett-Luce 複勝確率の数値的性質を検証する。
HorseRating には依存せず、最小の FakeHorse(total_rating/horse_id/horse_number)
でダックタイピング入力をテストする。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from utils.probability_engine import (
    DEFAULT_TEMPERATURE,
    MIN_TEMPERATURE,
    PROBABILITY_SCHEMA_VERSION,
    HorseProbability,
    compute_race_probabilities,
    _plackett_luce_place_probs,
)


@dataclass
class FakeHorse:
    """compute_race_probabilities がダックタイピングで読む最小属性のみ。"""
    horse_id: str
    horse_number: int
    total_rating: int


def _make_horses(ratings: list[int]) -> list[FakeHorse]:
    """rating のリストから馬番 1..N の FakeHorse を作る。"""
    return [
        FakeHorse(horse_id=f"h{i+1}", horse_number=i + 1, total_rating=r)
        for i, r in enumerate(ratings)
    ]


# =====================================================================
# softmax 単勝確率の基本性質
# =====================================================================

def test_win_prob_sums_to_one():
    """単勝確率の合計は 1.0 ± 1e-6。"""
    horses = _make_horses([165, 120, 95, 80, 50, 0])
    probs = compute_race_probabilities(horses, temperature=DEFAULT_TEMPERATURE)
    total = sum(p.win_prob for p in probs)
    assert abs(total - 1.0) < 1e-6


def test_higher_rating_higher_win_prob():
    """rating が高い馬ほど単勝確率が高い。"""
    horses = _make_horses([165, 120, 95, 80])
    probs = {p.horse_number: p for p in compute_race_probabilities(horses)}
    assert probs[1].win_prob > probs[2].win_prob > probs[3].win_prob > probs[4].win_prob


def test_equal_ratings_give_equal_probs():
    """全馬同 rating なら全頭等確率(1/N)。"""
    horses = _make_horses([50, 50, 50, 50, 50])
    probs = compute_race_probabilities(horses)
    for p in probs:
        assert abs(p.win_prob - 0.2) < 1e-9


def test_larger_temperature_flattens_distribution():
    """T が大きいほど分布はフラット(最大-最小の差が縮む)。"""
    horses = _make_horses([165, 120, 95, 80, 50])
    sharp = compute_race_probabilities(horses, temperature=10.0)
    flat = compute_race_probabilities(horses, temperature=50.0)
    spread_sharp = max(p.win_prob for p in sharp) - min(p.win_prob for p in sharp)
    spread_flat = max(p.win_prob for p in flat) - min(p.win_prob for p in flat)
    assert spread_flat < spread_sharp


def test_temperature_clamped_to_minimum():
    """MIN_TEMPERATURE 未満の T を渡してもエラーにならず確率合計 1.0。"""
    horses = _make_horses([100, 50, 0])
    probs = compute_race_probabilities(horses, temperature=0.0)
    assert abs(sum(p.win_prob for p in probs) - 1.0) < 1e-6
    # 0 は MIN にクランプされている(極端だが計算は成立)
    assert MIN_TEMPERATURE >= 1.0


def test_no_overflow_with_huge_ratings():
    """非現実的に大きな rating でもオーバーフローせず確率合計 1.0。"""
    horses = _make_horses([100000, 99000, 1])
    probs = compute_race_probabilities(horses, temperature=DEFAULT_TEMPERATURE)
    assert abs(sum(p.win_prob for p in probs) - 1.0) < 1e-6
    assert all(np.isfinite(p.win_prob) for p in probs)


# =====================================================================
# 除外馬(B1/B2)
# =====================================================================

def test_excluded_horse_zero_prob():
    """除外馬は win_prob/place_prob=0、is_excluded=True、win_rank=99。"""
    horses = _make_horses([165, 120, 95])
    probs = {p.horse_number: p for p in compute_race_probabilities(
        horses, excluded_ids={"h1"},
    )}
    assert probs[1].is_excluded is True
    assert probs[1].win_prob == 0.0
    assert probs[1].place_prob == 0.0
    assert probs[1].win_rank == 99


def test_excluded_does_not_affect_active_sum():
    """除外馬を抜いた active 馬の単勝確率合計は 1.0(除外馬は計算から外れる)。"""
    horses = _make_horses([165, 120, 95, 80])
    probs = compute_race_probabilities(horses, excluded_ids={"h1", "h2"})
    active_sum = sum(p.win_prob for p in probs if not p.is_excluded)
    assert abs(active_sum - 1.0) < 1e-6
    assert sum(1 for p in probs if p.is_excluded) == 2


def test_all_excluded_returns_all_zero():
    """全頭除外なら全馬 0 確率(クラッシュしない)。"""
    horses = _make_horses([100, 50])
    probs = compute_race_probabilities(horses, excluded_ids={"h1", "h2"})
    assert all(p.win_prob == 0.0 and p.is_excluded for p in probs)


# =====================================================================
# Plackett-Luce 複勝確率
# =====================================================================

def test_place_prob_sum_equals_n_places():
    """複勝確率(1-3 着内)の合計は n_places=3 ± 数値誤差。"""
    horses = _make_horses([165, 120, 95, 80, 50, 30, 10])
    probs = compute_race_probabilities(horses)
    total_place = sum(p.place_prob for p in probs)
    assert abs(total_place - 3.0) < 1e-6


def test_place_prob_ge_win_prob():
    """各馬の複勝確率 ≥ 単勝確率(1 着は 1-3 着内に含まれる)。"""
    horses = _make_horses([165, 120, 95, 80, 50])
    probs = compute_race_probabilities(horses)
    for p in probs:
        assert p.place_prob >= p.win_prob - 1e-9


def test_place_prob_bounded_0_1():
    """複勝確率は [0, 1] に収まる。"""
    horses = _make_horses([200, 150, 100, 50, 0])
    probs = compute_race_probabilities(horses)
    for p in probs:
        assert 0.0 <= p.place_prob <= 1.0


def test_plackett_luce_two_horses_all_placed():
    """2 頭立てなら両馬とも 1-3 着内確定 → 複勝確率 = 1.0 ずつ(合計 2.0)。"""
    win = np.array([0.6, 0.4])
    place = _plackett_luce_place_probs(win, n_places=3)
    assert abs(place[0] - 1.0) < 1e-9
    assert abs(place[1] - 1.0) < 1e-9


def test_plackett_luce_three_horses_all_placed():
    """3 頭立てなら全馬 1-3 着内確定 → 複勝確率 = 1.0 ずつ。"""
    win = np.array([0.5, 0.3, 0.2])
    place = _plackett_luce_place_probs(win, n_places=3)
    assert np.allclose(place, [1.0, 1.0, 1.0], atol=1e-9)


# =====================================================================
# win_rank / 並び順 / 端ケース
# =====================================================================

def test_win_rank_assignment():
    """win_rank は単勝確率降順で 1..N。最高 rating 馬が rank 1。"""
    horses = _make_horses([80, 165, 95])  # 馬番 1,2,3、rating 80/165/95
    probs = {p.horse_number: p for p in compute_race_probabilities(horses)}
    assert probs[2].win_rank == 1  # rating 165
    assert probs[3].win_rank == 2  # rating 95
    assert probs[1].win_rank == 3  # rating 80


def test_result_sorted_by_horse_number():
    """戻り値は馬番昇順で安定ソートされている。"""
    horses = _make_horses([10, 200, 50, 100])
    probs = compute_race_probabilities(horses)
    numbers = [p.horse_number for p in probs]
    assert numbers == sorted(numbers)


def test_empty_input_returns_empty():
    """空入力は空リスト。"""
    assert compute_race_probabilities([]) == []


def test_single_horse_win_prob_one():
    """1 頭だけなら単勝確率 1.0、複勝確率 1.0。"""
    horses = _make_horses([100])
    probs = compute_race_probabilities(horses)
    assert len(probs) == 1
    assert abs(probs[0].win_prob - 1.0) < 1e-9
    assert abs(probs[0].place_prob - 1.0) < 1e-9
    assert probs[0].win_rank == 1


def test_returns_horse_probability_dataclass():
    """戻り値要素は HorseProbability で必要属性を保持する。"""
    horses = _make_horses([100, 50])
    probs = compute_race_probabilities(horses)
    p = probs[0]
    assert isinstance(p, HorseProbability)
    assert hasattr(p, "win_prob") and hasattr(p, "place_prob")
    assert hasattr(p, "win_rank") and hasattr(p, "is_excluded")
    assert p.rating in (100.0, 50.0)


def test_schema_version_present():
    """スキーマバージョン定数が定義され空でない。"""
    assert isinstance(PROBABILITY_SCHEMA_VERSION, str)
    assert PROBABILITY_SCHEMA_VERSION
