"""
本ロジック v1.0 / Step 1 ○マーク収集ルールエンジンのユニットテスト。

実行: python -m pytest tests/test_onmark_rules.py -v
依存: pytest 不要、`python tests/test_onmark_rules.py` で assert 走る。

Phase 2 の対象範囲:
- Rules 9〜22(評価関数の挙動)
- Rule 24(休養明け検出)
- collect_onmarks(直近5走全評価 + Rule 24 救済)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.onmark_rules import (  # noqa: E402
    RULES_9_TO_22,
    collect_onmarks,
    detect_rule_24_situation,
    evaluate_rule,
    has_pass_order_improvement,
    is_dry_going,
    is_heavy_going,
)


def _r(n: int):
    """ルール番号からスペック取得"""
    return next(r for r in RULES_9_TO_22 if r.rule_no == n)


# ==================================================================
# 馬場分類ヘルパ
# ==================================================================
def test_going_classification():
    assert is_dry_going("良") is True
    assert is_dry_going("稍重") is False  # spec が良/重二分なので False
    assert is_dry_going("重") is False
    assert is_dry_going("不良") is False

    assert is_heavy_going("重") is True
    assert is_heavy_going("不良") is True
    assert is_heavy_going("良") is False
    assert is_heavy_going("稍重") is False


# ==================================================================
# 通過順位改善ヘルパ
# ==================================================================
def test_pass_order_improvement():
    # 10→8→5→3 = strict 改善
    assert has_pass_order_improvement(
        {"corner_1": 10, "corner_2": 8, "corner_3": 5, "corner_4": 3}
    ) is True
    # 同位 = 改善なし
    assert has_pass_order_improvement(
        {"corner_1": 6, "corner_2": 6, "corner_3": 6, "corner_4": 6}
    ) is False
    # 1コーナーのみ valid → False
    assert has_pass_order_improvement(
        {"corner_1": 5, "corner_2": None, "corner_3": None, "corner_4": None}
    ) is False
    # 1→4 (悪化)
    assert has_pass_order_improvement(
        {"corner_1": 2, "corner_2": 4, "corner_3": 5, "corner_4": 6}
    ) is False


# ==================================================================
# 個別ルール挙動
# ==================================================================
def test_rule15_normal_track_threshold():
    # クロワデュノール 東京優駿 (芝2400 良 上3F 34.2 通過 4-3-2-3) は東京なので閾値 35.0
    run = {
        "racecourse": "東京", "surface": "芝", "distance": 2400, "going": "良",
        "last_3f": 34.2, "corner_1": 4, "corner_2": 3, "corner_3": 2, "corner_4": 3,
    }
    ok, reason = evaluate_rule(_r(15), run)
    assert ok is True
    assert "R15" in reason
    assert "34.2<35.0" in reason


def test_rule13_special_track_threshold():
    # 阪神 芝2000 良で 34.4秒 → 通常閾値34.0なら NG、阪神特例34.5なら OK
    run = {
        "racecourse": "阪神", "surface": "芝", "distance": 2000, "going": "良",
        "last_3f": 34.4, "corner_1": 8, "corner_2": 6, "corner_3": 5, "corner_4": 4,
    }
    ok, reason = evaluate_rule(_r(13), run)
    assert ok is True, "阪神は 34.5 まで OK"
    assert "阪神特例" in reason


def test_rule13_no_improvement_blocks():
    # 阪神 芝2000 良 34.1 通過 6-6-6-6(改善なし)→ 該当しない
    run = {
        "racecourse": "阪神", "surface": "芝", "distance": 2000, "going": "良",
        "last_3f": 34.1, "corner_1": 6, "corner_2": 6, "corner_3": 6, "corner_4": 6,
    }
    ok, _ = evaluate_rule(_r(13), run)
    assert ok is False


def test_rule12_no_improvement_required():
    # 芝1600 重 35.5(<35.0は False、ボーダー 34.9 にして OK)、改善なしでも該当
    run = {
        "racecourse": "京都", "surface": "芝", "distance": 1600, "going": "重",
        "last_3f": 34.9, "corner_1": 5, "corner_2": 5, "corner_3": 5, "corner_4": 5,
    }
    ok, _ = evaluate_rule(_r(12), run)
    assert ok is True, "Rule 12 は通過順位改善不要"


def test_yaja_going_excluded():
    """稍重(中間)はどのルールでも該当しない(spec 通り保守的)"""
    run = {
        "racecourse": "東京", "surface": "芝", "distance": 1800, "going": "稍重",
        "last_3f": 34.0, "corner_1": 5, "corner_2": 4, "corner_3": 3, "corner_4": 2,
    }
    for rule in RULES_9_TO_22:
        ok, _ = evaluate_rule(rule, run)
        assert ok is False, f"稍重は Rule {rule.rule_no} に該当しないはず"


# ==================================================================
# Rule 24 検出
# ==================================================================
def test_rule24_triggers_on_long_layoff_and_poor_finish():
    past = [
        {"race_date": "2026-04-05", "finishing_position": 8},  # 前走
        {"race_date": "2025-09-15", "finishing_position": 1},  # 2走前(>180日空き)
    ]
    assert detect_rule_24_situation(past) is True


def test_rule24_skipped_when_layoff_short():
    past = [
        {"race_date": "2026-04-05", "finishing_position": 8},
        {"race_date": "2026-02-10", "finishing_position": 1},  # 2ヶ月弱
    ]
    assert detect_rule_24_situation(past) is False


def test_rule24_skipped_when_finish_good():
    # 180日休養明けでも前走3着なら凡走じゃないので発動しない
    past = [
        {"race_date": "2026-04-05", "finishing_position": 3},
        {"race_date": "2025-09-15", "finishing_position": 1},
    ]
    assert detect_rule_24_situation(past) is False


# ==================================================================
# collect_onmarks 統合
# ==================================================================
def test_collect_onmarks_aggregates_distinct_rules_across_runs():
    """異なる距離・馬場の過去走で別ルールが該当 → ○ 累積"""
    past_runs = [
        # 前走: 芝1400 良 33.0 改善 → R9
        {
            "race_date": "2026-04-05", "racecourse": "東京",
            "surface": "芝", "distance": 1400, "going": "良",
            "last_3f": 33.0, "corner_1": 5, "corner_2": 4, "corner_3": 3, "corner_4": 2,
            "finishing_position": 1,
        },
        # 2走前: 芝1600 良 34.0 改善 → R11
        {
            "race_date": "2026-03-01", "racecourse": "東京",
            "surface": "芝", "distance": 1600, "going": "良",
            "last_3f": 34.0, "corner_1": 6, "corner_2": 5, "corner_3": 4, "corner_4": 3,
            "finishing_position": 2,
        },
        # 3走前: 芝1800 良 33.8 改善 → R13
        {
            "race_date": "2026-02-01", "racecourse": "東京",
            "surface": "芝", "distance": 1800, "going": "良",
            "last_3f": 33.8, "corner_1": 4, "corner_2": 3, "corner_3": 3, "corner_4": 2,
            "finishing_position": 1,
        },
    ]
    n_marks, reasons = collect_onmarks(past_runs)
    # R9 + R11 + R13 = 3 個
    assert n_marks == 3, f"expected 3, got {n_marks} ({reasons})"
    assert any("R9" in r for r in reasons)
    assert any("R11" in r for r in reasons)
    assert any("R13" in r for r in reasons)


def test_collect_onmarks_same_rule_only_once():
    """同じルールが2走で該当しても ○は 1 個"""
    past_runs = [
        # 前走: R15 該当
        {
            "race_date": "2026-04-05", "racecourse": "東京",
            "surface": "芝", "distance": 2400, "going": "良",
            "last_3f": 34.5, "corner_1": 5, "corner_2": 4, "corner_3": 3, "corner_4": 2,
            "finishing_position": 1,
        },
        # 2走前: 同じく R15 該当
        {
            "race_date": "2026-03-01", "racecourse": "京都",
            "surface": "芝", "distance": 3000, "going": "良",
            "last_3f": 34.0, "corner_1": 6, "corner_2": 5, "corner_3": 4, "corner_4": 3,
            "finishing_position": 2,
        },
    ]
    n_marks, reasons = collect_onmarks(past_runs)
    assert n_marks == 1
    assert "R15" in reasons[0]


def test_rule24_swaps_evaluation_target():
    """休養明け+前走凡走 → 前走を無視して 2,3走前 を評価対象にする"""
    past_runs = [
        # 前走: 休養明け凡走(180日空き、5着以下)
        {
            "race_date": "2026-04-05", "racecourse": "東京",
            "surface": "芝", "distance": 1600, "going": "良",
            "last_3f": 33.5, "corner_1": 1, "corner_2": 1, "corner_3": 1, "corner_4": 1,
            "finishing_position": 8,
        },
        # 2走前: 評価対象になる R13
        {
            "race_date": "2025-09-15", "racecourse": "東京",
            "surface": "芝", "distance": 2000, "going": "良",
            "last_3f": 33.8, "corner_1": 4, "corner_2": 3, "corner_3": 2, "corner_4": 1,
            "finishing_position": 1,
        },
        # 3走前: 評価対象になる R15
        {
            "race_date": "2025-06-01", "racecourse": "東京",
            "surface": "芝", "distance": 2400, "going": "良",
            "last_3f": 34.0, "corner_1": 5, "corner_2": 4, "corner_3": 3, "corner_4": 2,
            "finishing_position": 1,
        },
    ]
    n_marks, reasons = collect_onmarks(past_runs)
    # 前走 (R11 該当のはず) はカウントされない、2走前 R13 と 3走前 R15 のみ
    rule_nos = sorted(int(r.split(":")[0].lstrip("R")) for r in reasons if r.startswith("R") and ":" in r)
    assert 11 not in rule_nos, "Rule 24 適用時、前走由来の R11 は除外される"
    assert 13 in rule_nos
    assert 15 in rule_nos
    assert any("R24" in r for r in reasons)


def _all_tests():
    """単独実行用 — pytest なしで全テストを順に走らせる"""
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    fails = []
    for f in funcs:
        try:
            f()
            print(f"  ✓ {f.__name__}")
        except AssertionError as e:
            print(f"  ✗ {f.__name__}: {e}")
            fails.append(f.__name__)
        except Exception as e:
            print(f"  ✗ {f.__name__}: {type(e).__name__}: {e}")
            fails.append(f.__name__)
    print(f"\n{len(funcs) - len(fails)}/{len(funcs)} passed")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(_all_tests())
