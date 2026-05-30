"""scripts/calibrate_probability_v12.py の純粋メトリクス関数の単体テスト。

Brier / LogLoss / 信頼性図 binning / ECE を手計算値と突合する。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from calibrate_probability_v12 import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    log_loss,
    reliability_bins,
)


def test_brier_perfect_prediction():
    """完璧な予測(確率 1/0 が当たり/外れに一致)なら Brier = 0。"""
    assert brier_score([1.0, 0.0, 0.0], [1, 0, 0]) == 0.0


def test_brier_known_value():
    """手計算: p=[0.5,0.5], o=[1,0] → mean(0.25, 0.25) = 0.25。"""
    assert abs(brier_score([0.5, 0.5], [1, 0]) - 0.25) < 1e-12


def test_log_loss_known_value():
    """手計算: p=0.5, o=1 → -log(0.5) = 0.6931...。"""
    assert abs(log_loss([0.5], [1]) - (-math.log(0.5))) < 1e-9


def test_log_loss_clips_extremes():
    """p=0 で o=1 でも eps クリップで inf にならない(有限値)。"""
    val = log_loss([0.0], [1])
    assert math.isfinite(val) and val > 0


def test_reliability_bins_counts_and_rates():
    """bin 振り分けと実勝率の計算が正しい。"""
    probs = [0.05, 0.15, 0.16, 0.95]
    outcomes = [0, 1, 0, 1]
    bins = reliability_bins(probs, outcomes, n_bins=10)
    # 0-10% bin: 0.05 のみ、勝率 0
    assert bins[0]["count"] == 1
    assert bins[0]["actual_rate"] == 0.0
    # 10-20% bin: 0.15, 0.16 の 2 件、勝率 0.5
    assert bins[1]["count"] == 2
    assert abs(bins[1]["actual_rate"] - 0.5) < 1e-12
    # 90-100% bin(最終 bin は右端含む): 0.95 のみ、勝率 1.0
    assert bins[9]["count"] == 1
    assert bins[9]["actual_rate"] == 1.0


def test_reliability_bins_sum_to_total():
    """全 bin の件数合計 = 入力サンプル数。"""
    probs = [0.0, 0.1, 0.25, 0.5, 0.75, 1.0]
    outcomes = [0, 0, 1, 0, 1, 1]
    bins = reliability_bins(probs, outcomes, n_bins=10)
    assert sum(b["count"] for b in bins) == len(probs)


def test_expected_calibration_error_zero_when_calibrated():
    """予測=実勝率 が全 bin で一致なら ECE = 0。"""
    # bin 内 mean_pred == actual_rate を作る: 各馬 prob 0.5、半分当たり
    probs = [0.5, 0.5, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    bins = reliability_bins(probs, outcomes, n_bins=10)
    ece = expected_calibration_error(bins, len(probs))
    assert abs(ece - 0.0) < 1e-12


def test_empty_inputs_return_nan():
    """空入力は nan を返す(クラッシュしない)。"""
    assert math.isnan(brier_score([], []))
    assert math.isnan(log_loss([], []))
