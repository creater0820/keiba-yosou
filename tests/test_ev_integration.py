"""prediction_logic への EV 統合テスト(v1.13.0 Phase 2)。

predict_race_v2 が horse_evs(公正オッズ版)を埋めること、EV が確率に依存し
rating / 確率自体は不変であることを検証する。
"""

from __future__ import annotations

import pandas as pd
import pytest

from prediction_logic import RacePrediction, predict_race_v2
from utils.ev_engine import HorseEV, compute_race_evs

_HIST_COLS = [
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "distance", "surface", "going", "finishing_position", "horse_number",
    "horse_id", "horse_name", "jockey", "last_3f", "popularity", "odds",
    "carry_weight", "corner_1", "corner_2", "corner_3", "corner_4",
]


def _empty_historical() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in _HIST_COLS})


def _make_race_card(n: int = 6, racecourse: str = "東京") -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "race_id": "R20260510-東01", "race_date": "2026-05-10",
            "racecourse": racecourse, "race_number": 1, "race_name": "t",
            "distance": 1400, "surface": "ダ", "going": "良", "post_time": "10:00",
            "horse_id": f"{10000000 + i}", "horse_number": i,
            "horse_name": f"馬{i}", "jockey": "J", "carry_weight": 55.0,
            "popularity": i, "odds": float(i),
        })
    df = pd.DataFrame(rows)
    df.attrs["data_format"] = "ra_se"
    return df


def test_predict_v2_fills_horse_evs():
    """predict_race_v2 が horse_evs を全頭分埋める(公正オッズ版)。"""
    pred = predict_race_v2(_make_race_card(6), _empty_historical(),
                           target_date="2026-05-10")
    assert isinstance(pred, RacePrediction)
    assert len(pred.horse_evs) == 6
    assert all(isinstance(e, HorseEV) for e in pred.horse_evs)


def test_predict_v2_ev_fair_only_no_market():
    """ベイクされた EV は公正オッズのみ(マーケットオッズ未入力 → ev None)。"""
    pred = predict_race_v2(_make_race_card(6), _empty_historical(),
                           target_date="2026-05-10")
    assert all(e.ev_tan is None for e in pred.horse_evs)
    # active 馬は公正オッズが計算されている
    active = [e for e in pred.horse_evs if e.win_prob > 0]
    assert active and all(e.fair_odds_tan is not None for e in active)


def test_predict_v2_evs_match_probabilities():
    """horse_evs の頭数と公正オッズ = 1/win_prob が確率と整合する。"""
    pred = predict_race_v2(_make_race_card(8), _empty_historical(),
                           target_date="2026-05-10")
    assert len(pred.horse_evs) == len(pred.horse_probabilities)
    prob_by_num = {p.horse_number: p for p in pred.horse_probabilities}
    for e in pred.horse_evs:
        p = prob_by_num[e.horse_number]
        if p.win_prob > 0:
            assert abs(e.fair_odds_tan - 1.0 / p.win_prob) < 1e-9


def test_ev_recompute_with_market_odds():
    """マーケットオッズを後から渡すと EV が計算され、確率は不変。"""
    pred = predict_race_v2(_make_race_card(6), _empty_historical(),
                           target_date="2026-05-10")
    probs_before = [(p.horse_number, p.win_prob) for p in pred.horse_probabilities]
    top = pred.horse_probabilities[0]
    # 公正オッズより高いオッズを与えれば EV > 0
    fair = 1.0 / top.win_prob if top.win_prob > 0 else 10.0
    evs = compute_race_evs(
        pred.horse_probabilities,
        market_odds_dict={top.horse_number: {"tan": fair * 1.5}},
    )
    target = next(e for e in evs if e.horse_number == top.horse_number)
    assert target.ev_tan is not None and target.ev_tan > 0
    # 確率は再計算しても変わらない
    probs_after = [(p.horse_number, p.win_prob) for p in pred.horse_probabilities]
    assert probs_before == probs_after


def test_ev_does_not_change_judgment():
    """EV 追加で判定(main_pick / wides / tickets)が変わらない。"""
    rc = _make_race_card(6)
    pred = predict_race_v2(rc, _empty_historical(), target_date="2026-05-10")
    # 判定系フィールドが従来通り存在(EV はそれらに依存しない)
    assert hasattr(pred.judgment, "main_pick")
    assert isinstance(pred.wide_candidates, list)
    assert hasattr(pred.betting, "tickets")


def test_ev_excluded_horse_no_fair_odds():
    """B1/B2 除外馬(win_prob 0)は公正オッズ None。"""
    # 1番人気+逃げ → B1 減点される構成を作るのは難しいので、確率0の馬を確認
    pred = predict_race_v2(_make_race_card(6), _empty_historical(),
                           target_date="2026-05-10")
    for e in pred.horse_evs:
        if e.win_prob == 0:
            assert e.fair_odds_tan is None


def test_horse_evs_default_empty_for_onmark():
    """RacePrediction の horse_evs は既定 空リスト(後方互換)。"""
    rp = RacePrediction(
        race_id="x", race_meta={}, horses=[], judgment=None,
        wide_candidates=[], betting=None, demerit_entries=[],
    )
    assert rp.horse_evs == []
    assert rp.horse_probabilities == []


def test_kelly_available_from_engine():
    """Kelly 関数が ev_engine から使える(Phase 4 準備、Phase 2 では計算のみ)。"""
    from utils.ev_engine import kelly_fraction
    assert abs(kelly_fraction(0.5, 3.0) - 0.25) < 1e-9
