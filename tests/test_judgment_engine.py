"""
本ロジック v1.0 / Step 2-4 判定エンジンのユニットテスト。
実行: python tests/test_judgment_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.judgment_engine import (  # noqa: E402
    HorseMarkData,
    detect_demerit_horses,
    determine_main_pick,
    extract_wide_candidates,
)


def _h(
    *,
    horse_id="H1",
    horse_name="Test",
    horse_number=1,
    frame_number=1,
    popularity=1,
    running_style="先行",
    marks_count=0,
    last_finishing_position=None,
):
    """テスト用 HorseMarkData ファクトリ"""
    return HorseMarkData(
        horse_id=horse_id, horse_name=horse_name, horse_number=horse_number,
        frame_number=frame_number, popularity=popularity,
        running_style=running_style, marks_count=marks_count,
        matched_rules=[], last_finishing_position=last_finishing_position,
    )


# ==================================================================
# 減点ルール
# ==================================================================
def test_rule6_demerit_fav_runaway():
    """1番人気 + 逃げ → rule_6 で2着以下扱い"""
    horses = [_h(popularity=1, running_style="逃げ", horse_id="A")]
    out = detect_demerit_horses(horses, {"racecourse": "東京", "distance": 2400})
    assert len(out) == 1
    assert out[0].rule_id == "rule_6"
    assert out[0].downgrade_to == 2


def test_rule6_no_demerit_when_not_runaway():
    """1番人気でも 先行 なら該当しない"""
    horses = [_h(popularity=1, running_style="先行")]
    assert detect_demerit_horses(horses, {"racecourse": "東京", "distance": 2400}) == []


def test_rule7_demerit_hanshin_mile_outer_frame():
    """阪神1600m + 7枠 → rule_7 で3着以下扱い"""
    horses = [_h(frame_number=7, horse_id="A")]
    out = detect_demerit_horses(horses, {"racecourse": "阪神", "distance": 1600})
    assert len(out) == 1
    assert out[0].rule_id == "rule_7"
    assert out[0].downgrade_to == 3


def test_rule7_no_demerit_inner_frame():
    """阪神1600m でも 4枠なら該当しない"""
    horses = [_h(frame_number=4)]
    assert detect_demerit_horses(horses, {"racecourse": "阪神", "distance": 1600}) == []


def test_rule7_no_demerit_other_track_or_distance():
    """京都1600や阪神2000では該当しない"""
    horses = [_h(frame_number=8)]
    assert detect_demerit_horses(horses, {"racecourse": "京都", "distance": 1600}) == []
    assert detect_demerit_horses(horses, {"racecourse": "阪神", "distance": 2000}) == []


# ==================================================================
# 本命判定
# ==================================================================
def test_main_pick_picks_lowest_popularity_among_candidates():
    """○≥5 が複数なら人気上位(=数字小)で決まる"""
    horses = [
        _h(horse_id="A", popularity=2, marks_count=5),
        _h(horse_id="B", popularity=1, marks_count=6),
        _h(horse_id="C", popularity=3, marks_count=7),
    ]
    res = determine_main_pick(horses, {"racecourse": "東京", "distance": 2000})
    assert res.main_pick == "B"
    assert res.sub_pick is None


def test_main_pick_excludes_demerited_then_falls_back():
    """○≥5でも減点で除外されたら次点(該当ゼロなら準本命へ)"""
    horses = [
        _h(horse_id="A", popularity=1, running_style="逃げ", marks_count=6),  # rule_6 除外
        _h(horse_id="B", popularity=5, running_style="先行", marks_count=2),
    ]
    res = determine_main_pick(horses, {"racecourse": "東京", "distance": 2400})
    assert res.main_pick is None  # ○≥5 が除外で残ゼロ
    assert res.sub_pick == "B"      # 準本命=最高○マークの馬
    assert "A" in res.excluded_by_demerit


def test_main_pick_zero_candidates_uses_subpick():
    """全頭○<5 → 準本命=最高○の馬"""
    horses = [
        _h(horse_id="A", popularity=5, marks_count=2),
        _h(horse_id="B", popularity=10, marks_count=3),
        _h(horse_id="C", popularity=1, marks_count=1),
    ]
    res = determine_main_pick(horses, {"racecourse": "東京", "distance": 2000})
    assert res.main_pick is None
    assert res.sub_pick == "B"
    assert res.sub_pick_marks == 3


# ==================================================================
# ワイド候補
# ==================================================================
def test_wide_candidate_rule3_adjacent_to_fav():
    """1番人気の隣枠 + 7番人気以降 → rule_3"""
    horses = [
        _h(horse_id="FAV", horse_number=5, frame_number=4, popularity=1),
        _h(horse_id="ADJ", horse_number=8, frame_number=5, popularity=8),
        _h(horse_id="FAR", horse_number=12, frame_number=8, popularity=10),
    ]
    out = extract_wide_candidates(horses, {"racecourse": "東京"})
    ids = [c.horse_id for c in out]
    assert "ADJ" in ids
    assert "FAR" not in ids


def test_wide_candidate_rule4_bad_finish():
    """前走 5/7/9/11/13着 → rule_4"""
    horses = [
        _h(horse_id="A", popularity=4, last_finishing_position=5),
        _h(horse_id="B", popularity=4, last_finishing_position=6),  # 6着は対象外
        _h(horse_id="C", popularity=4, last_finishing_position=11),
    ]
    out = extract_wide_candidates(horses, {"racecourse": "東京"})
    ids = [c.horse_id for c in out]
    assert "A" in ids and "C" in ids and "B" not in ids


def test_wide_candidate_rule5_one_frame_runaway():
    """1枠の逃げ + 5番人気以降"""
    horses = [
        _h(horse_id="A", frame_number=1, running_style="逃げ", popularity=6),
        _h(horse_id="B", frame_number=1, running_style="逃げ", popularity=2),  # 4番人気以内は対象外
        _h(horse_id="C", frame_number=2, running_style="逃げ", popularity=8),  # 1枠でないので対象外
    ]
    out = extract_wide_candidates(horses, {"racecourse": "東京"})
    ids = [c.horse_id for c in out]
    assert "A" in ids and "B" not in ids and "C" not in ids


def test_wide_candidate_rule8_kokura_chukyo():
    """小倉/中京 + 逃げ → rule_8"""
    horses = [_h(horse_id="A", running_style="逃げ", popularity=3)]
    out = extract_wide_candidates(horses, {"racecourse": "小倉"})
    assert len(out) == 1 and out[0].horse_id == "A"
    out2 = extract_wide_candidates(horses, {"racecourse": "中京"})
    assert len(out2) == 1
    out3 = extract_wide_candidates(horses, {"racecourse": "東京"})
    assert len(out3) == 0


def test_wide_candidate_max_3_priority_by_match_count():
    """複数該当馬を優先、3頭で打ち切り"""
    horses = [
        _h(horse_id="HIT2", frame_number=1, running_style="逃げ",
           popularity=5, last_finishing_position=7),  # rule_4 + rule_5 = 2 hits
        _h(horse_id="HIT1A", popularity=4, last_finishing_position=9),  # rule_4
        _h(horse_id="HIT1B", popularity=6, last_finishing_position=11), # rule_4
        _h(horse_id="HIT1C", popularity=10, last_finishing_position=13),# rule_4
    ]
    out = extract_wide_candidates(horses, {"racecourse": "東京"})
    assert len(out) == 3
    assert out[0].horse_id == "HIT2"          # 最多 hit が先頭
    assert out[0].priority == 2


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
