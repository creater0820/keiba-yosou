"""scripts/backtest_ev_v13.py の純粋ヘルパの単体テスト(v1.13.0 Phase 2)。

summarize_strategy / summarize_kelly / ev_histogram を手計算値と突合する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_ev_v13 import (  # noqa: E402
    ev_histogram,
    summarize_kelly,
    summarize_strategy,
    _valid_odds,
)


def test_summarize_strategy_known_values():
    """3 点ベット中 1 点的中(オッズ4.0)→ 的中率33.3%、回収率133.3%、収支+1.0。"""
    bets = [
        {"odds": 4.0, "win": True},
        {"odds": 3.0, "win": False},
        {"odds": 5.0, "win": False},
    ]
    s = summarize_strategy(bets)
    assert s["n"] == 3
    assert abs(s["hit_rate"] - 100 / 3) < 1e-9
    assert abs(s["payout_rate"] - 4.0 / 3 * 100) < 1e-9
    assert abs(s["net"] - (4.0 - 3)) < 1e-9


def test_summarize_strategy_empty():
    """空ベットは全 0(クラッシュしない)。"""
    s = summarize_strategy([])
    assert s == {"n": 0, "hit_rate": 0.0, "payout_rate": 0.0, "net": 0.0}


def test_summarize_strategy_all_hit():
    """全的中なら回収率 = 平均オッズ×100。"""
    bets = [{"odds": 2.0, "win": True}, {"odds": 4.0, "win": True}]
    s = summarize_strategy(bets)
    assert abs(s["payout_rate"] - 300.0) < 1e-9  # (2+4)/2*100
    assert s["hit_rate"] == 100.0


def test_summarize_kelly_known_values():
    """Kelly: stake 0.2 当たり(odds5) + stake 0.1 外れ → 回収率 = 1.0/0.3*100。"""
    bets = [
        {"odds": 5.0, "win": True, "stake": 0.2},
        {"odds": 3.0, "win": False, "stake": 0.1},
    ]
    s = summarize_kelly(bets)
    assert abs(s["staked"] - 0.3) < 1e-9
    assert abs(s["payout_rate"] - (0.2 * 5.0) / 0.3 * 100) < 1e-9
    assert abs(s["net"] - (0.2 * 5.0 - 0.3)) < 1e-9


def test_ev_histogram_counts():
    """EV 値が正しい bin に振り分けられる。"""
    values = [-0.3, 0.05, 0.05, 1.5]
    edges = [-1.0, 0.0, 0.5, 100.0]
    hist = ev_histogram(values, edges)
    assert hist[0]["count"] == 1   # [-1,0): -0.3
    assert hist[1]["count"] == 2   # [0,0.5): 0.05, 0.05
    assert hist[2]["count"] == 1   # [0.5,100]: 1.5
    assert sum(h["count"] for h in hist) == len(values)


def test_valid_odds_filters():
    """オッズの妥当性チェック(NaN/≤1.0/非数 → None)。"""
    assert _valid_odds(3.5) == 3.5
    assert _valid_odds(1.0) is None
    assert _valid_odds(0.0) is None
    assert _valid_odds(float("nan")) is None
    assert _valid_odds("abc") is None
    assert _valid_odds(None) is None
