"""
本ロジック v1.0 / Step 5 買い目戦略のユニットテスト。
実行: python tests/test_betting_strategy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.betting_strategy import (  # noqa: E402
    apply_dirt_heavy_correction,
    filter_by_frame_parity,
    generate_betting_recommendations,
)
from utils.judgment_engine import HorseMarkData, WideCandidate  # noqa: E402


def _h(**kw):
    defaults = dict(
        horse_id="X", horse_name="N", horse_number=1, frame_number=1,
        popularity=5, running_style="先行", marks_count=0,
        matched_rules=[], last_finishing_position=None,
    )
    defaults.update(kw)
    return HorseMarkData(**defaults)


def _w(**kw):
    defaults = dict(
        horse_id="X", horse_name="N", horse_number=1, popularity=5,
        matched_rules=["rule_4"], reasons=[], priority=1,
    )
    defaults.update(kw)
    return WideCandidate(**defaults)


# ==================================================================
# Rule 23 ダート不良補正
# ==================================================================
def test_rule23_dirt_heavy_runaway_gets_bonus():
    horses = [
        _h(horse_id="A", running_style="逃げ", marks_count=2),
        _h(horse_id="B", running_style="先行", marks_count=2),
    ]
    out = apply_dirt_heavy_correction(horses, {"surface": "ダ", "going": "不良"})
    assert {h.horse_id: h.marks_count for h in out} == {"A": 3, "B": 2}
    assert any("R23" in r for r in next(h for h in out if h.horse_id == "A").matched_rules)


def test_rule23_no_bonus_when_track_or_going_mismatch():
    horses = [_h(horse_id="A", running_style="逃げ", marks_count=2)]
    # 芝・不良 → 対象外
    out1 = apply_dirt_heavy_correction(horses, {"surface": "芝", "going": "不良"})
    assert out1[0].marks_count == 2
    # ダ・重 → 対象外(spec は「不良」限定)
    out2 = apply_dirt_heavy_correction(horses, {"surface": "ダ", "going": "重"})
    assert out2[0].marks_count == 2


# ==================================================================
# Rule 2 偶奇フィルタ
# ==================================================================
def test_parity_filter_keeps_same_parity_only():
    horses = [
        _h(horse_id="FAV", popularity=1, frame_number=3),  # 奇数枠1番人気
        _h(horse_id="ODD1", popularity=5, frame_number=1),
        _h(horse_id="EVEN1", popularity=6, frame_number=2),
        _h(horse_id="ODD2", popularity=8, frame_number=7),
    ]
    candidates = [
        _w(horse_id="ODD1", popularity=5),
        _w(horse_id="EVEN1", popularity=6),
        _w(horse_id="ODD2", popularity=8),
    ]
    out = filter_by_frame_parity(candidates, horses)
    ids = [c.horse_id for c in out]
    assert "ODD1" in ids and "ODD2" in ids
    assert "EVEN1" not in ids


def test_parity_filter_no_fav_returns_unchanged():
    horses = [_h(horse_id="A", popularity=2, frame_number=1)]
    candidates = [_w(horse_id="A", popularity=2)]
    out = filter_by_frame_parity(candidates, horses)
    assert len(out) == 1


# ==================================================================
# 買い目生成
# ==================================================================
def test_bet_generation_full_set_with_3_wides():
    horses = [
        _h(horse_id="AXIS", horse_number=7, horse_name="本命馬", popularity=1),
        _h(horse_id="W1", horse_number=3, horse_name="W1馬", popularity=5),
        _h(horse_id="W2", horse_number=11, horse_name="W2馬", popularity=8),
        _h(horse_id="W3", horse_number=14, horse_name="W3馬", popularity=10),
    ]
    wides = [
        _w(horse_id="W1", horse_number=3, horse_name="W1馬", popularity=5),
        _w(horse_id="W2", horse_number=11, horse_name="W2馬", popularity=8),
        _w(horse_id="W3", horse_number=14, horse_name="W3馬", popularity=10),
    ]
    plan = generate_betting_recommendations("AXIS", None, wides, horses)
    types = [t.bet_type for t in plan.tickets]
    assert types.count("単勝") == 1
    assert types.count("複勝") == 1
    assert types.count("馬連") == 1
    assert types.count("三連複") == 1
    assert types.count("ワイド") == 3
    # 馬連: 軸+ワイドトップ
    bareta = next(t for t in plan.tickets if t.bet_type == "馬連")
    assert sorted(bareta.horse_numbers) == [3, 7]
    # 三連複: 軸+上位2頭
    sanren = next(t for t in plan.tickets if t.bet_type == "三連複")
    assert sorted(sanren.horse_numbers) == [3, 7, 11]
    # 軸ラベル ◎
    assert plan.main_horse_label.startswith("◎")


def test_bet_generation_subpick_label():
    horses = [_h(horse_id="SUB", horse_number=4, horse_name="準馬")]
    plan = generate_betting_recommendations(None, "SUB", [], horses)
    assert plan.main_horse_label.startswith("準◎")
    types = [t.bet_type for t in plan.tickets]
    # ワイド候補ゼロ → 単勝・複勝のみ生成
    assert types == ["単勝", "複勝"]


def test_bet_generation_no_axis_returns_empty():
    plan = generate_betting_recommendations(None, None, [], [])
    assert plan.tickets == []


def _all_tests():
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
