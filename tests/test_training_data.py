"""
utils/training_data.py のテスト(v1.5 で導入の坂路調教 CSV パーサ + F4/F5)。

テスト対象:
- parse_training_csv: Shift_JIS デコード、列名リネーム、数値変換
- match_training_to_horses: 馬名マッチ、target_date 以前の最新採用、馬番ラベル除外
- evaluate_f4_f5: F4 / F5 排他、境界値(11.2 ジャスト)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.training_data import (
    F4_F5_THRESHOLD,
    evaluate_f4_f5,
    match_training_to_horses,
    parse_training_csv,
)


# ==================================================================
# F4 / F5 評価(v1.7.5 緩和版: F5 lap1≤12.3 OR lap1+lap2≤24.8、
#                              F4 lap1≤12.5 OR lap1+lap2≤25.4)
# ==================================================================
def test_evaluate_f5_lap1_below_threshold():
    """lap1 ≤ 12.3 → F5(+40)発火(F5 が優先で F4 にはフォールスルーしない)。"""
    rule, rate, reason = evaluate_f4_f5({"lap1": 12.0, "lap2": 13.0})
    assert rule == "F5"
    assert rate == 40
    assert "12.0" in reason


def test_evaluate_f5_lap_2f_total_below_threshold():
    """lap1+lap2 ≤ 24.8(lap1 単独は閾値 over)→ F5 発火。"""
    rule, rate, reason = evaluate_f4_f5({"lap1": 12.4, "lap2": 12.3})
    # lap1=12.4 > 12.3 だが、合計 24.7 ≤ 24.8 なので F5
    assert rule == "F5"
    assert rate == 40


def test_evaluate_f4_only_when_lap1_between_thresholds():
    """lap1 ∈ (12.3, 12.5] かつ 1F+2F > 24.8 だが ≤ 25.4 → F4 発火。"""
    rule, rate, reason = evaluate_f4_f5({"lap1": 12.5, "lap2": 12.9})
    # lap1=12.5 ≤ 12.5 (F4 OK)、合計 25.4 ≤ 25.4(F4 OK)
    # lap1=12.5 > 12.3 (F5 NG)、合計 25.4 > 24.8 (F5 NG)
    assert rule == "F4"
    assert rate == 30


def test_evaluate_no_fire_when_both_above_thresholds():
    """lap1 > 12.5 かつ lap1+lap2 > 25.4 → 不発。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.6, "lap2": 13.0})
    assert rule is None
    assert rate == 0


def test_evaluate_boundary_f5_lap1_at_12_3():
    """境界値 lap1=12.3 ジャスト → F5 発火(≤ なので含む)。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.3, "lap2": 13.0})
    assert rule == "F5"
    assert rate == 40


def test_evaluate_boundary_f5_lap_2f_at_24_8():
    """境界値 lap1+lap2=24.8 ジャスト(lap1=12.4)→ F5 発火。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.4, "lap2": 12.4})
    # lap1+lap2 = 24.8 ≤ 24.8 (F5 OK), lap1 12.4 > 12.3 (F5 NG)
    assert rule == "F5"


def test_evaluate_boundary_f4_lap1_at_12_5():
    """境界値 lap1=12.5 ジャスト + lap2=13.0 → F4 発火(F5 不発)。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.5, "lap2": 13.0})
    assert rule == "F4"


def test_evaluate_boundary_f4_lap_2f_at_25_4():
    """境界値 lap1+lap2=25.4 ジャスト(lap1=12.7) → F4 発火。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.7, "lap2": 12.7})
    # lap1=12.7 > 12.5 だが 1F+2F=25.4 ≤ 25.4 で F4 発火
    assert rule == "F4"


def test_evaluate_lap2_missing_lap1_under_f5():
    """lap2 欠損 + lap1 ≤ 12.3 → F5 発火(lap1 単独条件で OK)。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.0, "lap2": None})
    assert rule == "F5"
    assert rate == 40


def test_evaluate_lap2_missing_lap1_in_f4_range():
    """lap2 欠損 + lap1 ∈ (12.3, 12.5] → F4 発火。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 12.5, "lap2": None})
    assert rule == "F4"
    assert rate == 30


def test_evaluate_lap2_missing_lap1_above_f4():
    """lap2 欠損 + lap1 > 12.5 → 不発(累積判定不能)。"""
    rule, rate, _ = evaluate_f4_f5({"lap1": 13.0, "lap2": None})
    assert rule is None
    assert rate == 0


def test_evaluate_none_input_returns_none():
    """training_data が None / 空 → 不発。"""
    assert evaluate_f4_f5(None) == (None, 0, None)
    assert evaluate_f4_f5({}) == (None, 0, None)


def test_evaluate_threshold_constants():
    """v1.7.5 の閾値定数(実測ベースの上位 12% / 25% 相当)。"""
    from utils.training_data import (
        F5_LAP1_THRESHOLD, F5_LAP_2F_TOTAL_THRESHOLD,
        F4_LAP1_THRESHOLD, F4_LAP_2F_TOTAL_THRESHOLD,
    )
    assert F5_LAP1_THRESHOLD == 12.3
    assert F5_LAP_2F_TOTAL_THRESHOLD == 24.8
    assert F4_LAP1_THRESHOLD == 12.5
    assert F4_LAP_2F_TOTAL_THRESHOLD == 25.4
    # F4 は F5 より緩い境界(穴馬を広く拾う)
    assert F4_LAP1_THRESHOLD > F5_LAP1_THRESHOLD
    assert F4_LAP_2F_TOTAL_THRESHOLD > F5_LAP_2F_TOTAL_THRESHOLD


# ==================================================================
# F4穴 / F5穴(v1.7.5.1)— F4/F5 該当 + 人気 ≥ 6 番で追加加点
# ==================================================================
def test_f5_hole_fires_at_popularity_6():
    """F5 該当 + 6番人気ジャスト → F5穴(+20)発火。"""
    from utils.training_data import evaluate_f4_f5_hole
    rule, rate, _ = evaluate_f4_f5_hole("F5", 6)
    assert rule == "F5穴"
    assert rate == 20


def test_f4_hole_fires_at_popularity_8():
    """F4 該当 + 8番人気 → F4穴(+15)発火。"""
    from utils.training_data import evaluate_f4_f5_hole
    rule, rate, _ = evaluate_f4_f5_hole("F4", 8)
    assert rule == "F4穴"
    assert rate == 15


def test_hole_does_not_fire_at_popularity_5():
    """F4/F5 該当 + 5番人気以内 → 穴馬ボーナスなし(本命級は除外)。"""
    from utils.training_data import evaluate_f4_f5_hole
    assert evaluate_f4_f5_hole("F5", 5) == (None, 0, None)
    assert evaluate_f4_f5_hole("F5", 1) == (None, 0, None)
    assert evaluate_f4_f5_hole("F4", 3) == (None, 0, None)


def test_hole_does_not_fire_when_no_base_rule():
    """F4/F5 が発火していない馬には穴ボーナスを適用しない。"""
    from utils.training_data import evaluate_f4_f5_hole
    assert evaluate_f4_f5_hole(None, 8) == (None, 0, None)
    assert evaluate_f4_f5_hole("", 8) == (None, 0, None)


def test_hole_skips_unknown_popularity():
    """popularity が None / 0(取り込み未済)→ 穴ボーナス不発。"""
    from utils.training_data import evaluate_f4_f5_hole
    assert evaluate_f4_f5_hole("F5", None) == (None, 0, None)
    assert evaluate_f4_f5_hole("F5", 0) == (None, 0, None)


def test_hole_constants():
    """v1.7.5.1 の定数値が想定通り。"""
    from utils.training_data import (
        F_HOLE_MIN_POPULARITY, F4_HOLE_BONUS, F5_HOLE_BONUS,
    )
    assert F_HOLE_MIN_POPULARITY == 6
    assert F4_HOLE_BONUS == 15
    assert F5_HOLE_BONUS == 20
    # F5穴 > F4穴(より厳しい条件には大きい加点)
    assert F5_HOLE_BONUS > F4_HOLE_BONUS


# ==================================================================
# parse_training_csv: Shift_JIS デコード + 列リネーム
# ==================================================================
def _make_sjis_csv() -> bytes:
    """Shift_JIS でエンコードされた最小サンプル CSV を返す。"""
    text = (
        "場所,年月日,曜日,時刻,馬名,Ｃ,性別,年齢,収得賞金,調教師,"
        "Time1,Time2,Time3,Time4,Lap4,Lap3,Lap2,Lap1\n"
        "栗東,20260509,土,7:30,テストウマ,,牡,4,500,調教師A,"
        "55.0,40.0,27.0,13.0,15.0,13.0,12.5,12.0\n"
        "美浦,20260509,土,7:35,スピードホース,,牡,3,300,調教師B,"
        "53.0,38.0,25.0,11.0,15.0,13.0,11.0,11.0\n"
    )
    return text.encode("shift_jis")


def test_parse_training_csv_decodes_shift_jis():
    df = parse_training_csv(_make_sjis_csv())
    assert len(df) == 2
    assert "horse_name" in df.columns
    assert "lap1_time" in df.columns
    assert df["horse_name"].tolist() == ["テストウマ", "スピードホース"]


def test_parse_training_csv_numeric_conversion():
    df = parse_training_csv(_make_sjis_csv())
    # Lap1 列が float に変換されている
    assert df["lap1_time"].dtype.kind == "f"
    assert df["lap1_time"].iloc[0] == 12.0
    assert df["lap1_time"].iloc[1] == 11.0


def test_parse_training_csv_handles_utf8_fallback():
    """utf-8 で書かれた CSV も読める(fallback デコード)。"""
    text = (
        "場所,年月日,馬名,Lap1,Lap2\n"
        "栗東,20260509,アルファ,11.0,11.0\n"
    )
    df = parse_training_csv(text.encode("utf-8"))
    assert len(df) == 1
    assert df["horse_name"].iloc[0] == "アルファ"


# ==================================================================
# match_training_to_horses: 馬名マッチ + 日付フィルタ + 馬番ラベル除外
# ==================================================================
def test_match_training_to_horses_basic():
    """馬名完全一致で horse_id にデータが紐付く。"""
    training_df = pd.DataFrame({
        "horse_name": ["テストウマ", "スピードホース"],
        "training_date": ["20260509", "20260509"],
        "training_time": ["7:30", "7:35"],
        "lap1_time": [12.0, 11.0],
        "lap2_time": [12.5, 11.0],
        "place": ["栗東", "美浦"],
    })
    race_card = pd.DataFrame({
        "horse_id": ["A1", "A2"],
        "horse_name": ["テストウマ", "スピードホース"],
    })
    result = match_training_to_horses(training_df, race_card, target_date="20260509")
    assert "A1" in result
    assert "A2" in result
    assert result["A1"]["lap1"] == 12.0
    assert result["A2"]["lap1"] == 11.0


def test_match_skips_dc_failed_label_horses():
    """DC マッチ失敗馬の「馬番N(...)」ラベルは training に存在しないので除外。"""
    training_df = pd.DataFrame({
        "horse_name": ["テストウマ"],
        "training_date": ["20260509"],
        "training_time": ["7:30"],
        "lap1_time": [11.0],
        "lap2_time": [11.0],
    })
    race_card = pd.DataFrame({
        "horse_id": ["A1", "A2"],
        "horse_name": ["馬番3(DB照合不能)", "テストウマ"],
    })
    result = match_training_to_horses(training_df, race_card, target_date="20260509")
    assert "A1" not in result, "馬番ラベル馬は除外されるべき"
    assert "A2" in result


def test_match_uses_latest_when_multiple_trainings():
    """同一馬で複数日 → 最新日の調教を採用(レース当日以前の最新を取る方針)。"""
    training_df = pd.DataFrame({
        "horse_name": ["テストウマ", "テストウマ"],
        "training_date": ["20260506", "20260508"],  # 1 つは 3 日前、もう 1 つは前日
        "training_time": ["7:30", "7:30"],
        "lap1_time": [13.0, 11.5],  # 古い方が 13.0、新しい方が 11.5
        "lap2_time": [13.0, 11.5],
    })
    race_card = pd.DataFrame({
        "horse_id": ["A1"],
        "horse_name": ["テストウマ"],
    })
    result = match_training_to_horses(training_df, race_card, target_date="20260509")
    assert result["A1"]["lap1"] == 11.5, "最新の調教(20260508)を採用するはず"


def test_match_filters_out_future_trainings():
    """target_date より後の調教は除外(未来データの誤反映防止)。"""
    training_df = pd.DataFrame({
        "horse_name": ["テストウマ"],
        "training_date": ["20260601"],  # レース日(20260509)より後
        "training_time": ["7:30"],
        "lap1_time": [11.0],
        "lap2_time": [11.0],
    })
    race_card = pd.DataFrame({
        "horse_id": ["A1"],
        "horse_name": ["テストウマ"],
    })
    result = match_training_to_horses(training_df, race_card, target_date="20260509")
    assert result == {}, "未来日の調教は採用しない"


def test_match_returns_empty_for_empty_training_df():
    result = match_training_to_horses(
        pd.DataFrame(),
        pd.DataFrame({"horse_id": ["A"], "horse_name": ["X"]}),
    )
    assert result == {}
