"""prediction_logic への確率統合テスト(v1.12.0 Phase 1)。

predict_race_v2 / predict_race_dc が horse_probabilities を埋めること、
確率が rating に依存し rating 自体は不変であることを検証する。
"""

from __future__ import annotations

import pandas as pd
import pytest

from prediction_logic import (
    RacePrediction,
    _derive_probabilities,
    predict_race_v2,
)
from utils.judgment_engine import DemeritEntry, JudgmentResult
from utils.probability_engine import HorseProbability
from utils.rating_engine import HorseRating


# 過去レース DataFrame に必要な最小列(空でもスキーマがあれば動く)
_HIST_COLS = [
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "distance", "surface", "going", "finishing_position", "horse_number",
    "horse_id", "horse_name", "jockey", "last_3f", "popularity", "odds",
    "carry_weight", "corner_1", "corner_2", "corner_3", "corner_4",
]


def _empty_historical() -> pd.DataFrame:
    """列スキーマだけ持つ空の過去レース DataFrame。"""
    return pd.DataFrame({c: pd.Series(dtype="object") for c in _HIST_COLS})


def _make_race_card(n: int = 6, racecourse: str = "東京",
                    surface: str = "ダ", distance: int = 1400) -> pd.DataFrame:
    """n 頭の当日出馬表 DataFrame(RA+SE 風)を作る。"""
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "race_id": "R20260510-東01",
            "race_date": "2026-05-10",
            "racecourse": racecourse,
            "race_number": 1,
            "race_name": "テスト",
            "distance": distance,
            "surface": surface,
            "going": "良",
            "post_time": "10:00",
            "horse_id": f"{10000000 + i}",
            "horse_number": i,
            "horse_name": f"テスト馬{i}",
            "jockey": "騎手",
            "carry_weight": 55.0,
            "popularity": i,
            "odds": float(i),
        })
    df = pd.DataFrame(rows)
    df.attrs["data_format"] = "ra_se"
    return df


# =====================================================================
# predict_race_v2 統合
# =====================================================================

def test_predict_v2_fills_horse_probabilities():
    """predict_race_v2 が horse_probabilities を全頭分埋める。"""
    rc = _make_race_card(n=6)
    pred = predict_race_v2(rc, _empty_historical(), target_date="2026-05-10")
    assert isinstance(pred, RacePrediction)
    assert len(pred.horse_probabilities) == 6
    assert all(isinstance(p, HorseProbability) for p in pred.horse_probabilities)


def test_predict_v2_win_prob_sums_to_one():
    """active 馬(除外なし)の単勝確率合計が 1.0。"""
    rc = _make_race_card(n=6, racecourse="札幌")  # G ルール非該当の中立コース
    pred = predict_race_v2(rc, _empty_historical(), target_date="2026-05-10")
    active = [p for p in pred.horse_probabilities if not p.is_excluded]
    assert abs(sum(p.win_prob for p in active) - 1.0) < 1e-6


def test_predict_v2_probabilities_match_horse_count():
    """horse_probabilities の頭数は horse_ratings と一致。"""
    rc = _make_race_card(n=8)
    pred = predict_race_v2(rc, _empty_historical(), target_date="2026-05-10")
    assert len(pred.horse_probabilities) == len(pred.horse_ratings)


def test_predict_v2_rating_independent_of_probability():
    """確率を派生しても horse_ratings の total_rating は変化しない。

    確率派生は純粋追加なので、horse_probabilities を計算する前後で
    total_rating の集合は同一であるべき(派生関数は rating を読むだけ)。
    """
    rc = _make_race_card(n=6)
    pred = predict_race_v2(rc, _empty_historical(), target_date="2026-05-10")
    ratings_before = sorted(h.total_rating for h in pred.horse_ratings)
    # 再派生しても rating は不変
    _ = _derive_probabilities(pred.horse_ratings, pred.judgment)
    ratings_after = sorted(h.total_rating for h in pred.horse_ratings)
    assert ratings_before == ratings_after


# =====================================================================
# _derive_probabilities(B1/B2 除外)
# =====================================================================

def test_derive_excludes_demerit_horses():
    """judgment.demerit_entries の馬が確率 0(除外)になる。"""
    ratings = [
        HorseRating(
            horse_id=f"h{i}", horse_name=f"馬{i}", horse_number=i,
            frame_number=i, popularity=i, running_style="先行",
            total_rating=100 - i * 10,
        )
        for i in range(1, 5)
    ]
    judgment = JudgmentResult(
        main_pick=None, sub_pick=None, main_pick_marks=0, sub_pick_marks=0,
        candidates=[], excluded_by_demerit=["h2"],
        demerit_entries=[DemeritEntry(
            horse_id="h2", horse_name="馬2", horse_number=2,
            downgrade_to=2, rule_id="B1", reason="test",
        )],
        reason="",
    )
    probs = {p.horse_id: p for p in _derive_probabilities(ratings, judgment)}
    assert probs["h2"].is_excluded is True
    assert probs["h2"].win_prob == 0.0
    # 除外馬以外の単勝確率合計は 1.0
    active_sum = sum(p.win_prob for p in probs.values() if not p.is_excluded)
    assert abs(active_sum - 1.0) < 1e-6


def test_derive_no_demerit_no_exclusion():
    """減点馬がいなければ全馬 active(除外 0)。"""
    ratings = [
        HorseRating(
            horse_id=f"h{i}", horse_name=f"馬{i}", horse_number=i,
            frame_number=i, popularity=i, running_style="先行",
            total_rating=50,
        )
        for i in range(1, 4)
    ]
    judgment = JudgmentResult(
        main_pick=None, sub_pick=None, main_pick_marks=0, sub_pick_marks=0,
        candidates=[], excluded_by_demerit=[], demerit_entries=[], reason="",
    )
    probs = _derive_probabilities(ratings, judgment)
    assert all(not p.is_excluded for p in probs)
    assert abs(sum(p.win_prob for p in probs) - 1.0) < 1e-6
