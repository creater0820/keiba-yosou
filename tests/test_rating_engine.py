"""
本ロジック v1.1 / rating engine のユニットテスト。

カテゴリ A〜F の代表ケース、C/D/E 排他処理、100点閾値到達、減点 B 適用後の
◎不在 fallback、F2 救済、F3 斤量 -3kg を網羅。

実行:
- python tests/test_rating_engine.py     # 単体実行(pytest 不要)
- python -m pytest tests/test_rating_engine.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.judgment_engine import (  # noqa: E402
    determine_main_pick_v2,
    extract_wide_candidates_v2,
)
from utils.rating_engine import (  # noqa: E402
    HorseRating,
    RatingHit,
    compute_horse_rating,
)
from utils.rating_rules import (  # noqa: E402
    HONMEI_RATING_THRESHOLD,
    RATING_RULES_C,
    RATING_RULES_D,
    RATING_RULES_E,
    RatingPolicy,
)


# ==================================================================
# 共通: 過去走 dict ヘルパ
# ==================================================================
def _run(
    *,
    surface="芝",
    distance=1400,
    going="良",
    last_3f=33.0,
    racecourse="東京",
    finishing_position=1,
    corners=(10, 5, 3, 2),
    carry_weight=56.0,
    race_date="2025-12-01",
):
    d = {
        "surface": surface,
        "distance": distance,
        "going": going,
        "last_3f": last_3f,
        "racecourse": racecourse,
        "finishing_position": finishing_position,
        "carry_weight": carry_weight,
        "race_date": race_date,
    }
    for i, c in enumerate(corners, start=1):
        d[f"corner_{i}"] = c
    return d


def _empty_runs(n: int = 5):
    return [None] * n


def _meta(**overrides):
    base = {"racecourse": "東京", "race_number": 1, "distance": 1400,
            "surface": "芝", "going": "良"}
    base.update(overrides)
    return base


def _compute(past_runs=None, race_meta=None, **horse_overrides):
    """compute_horse_rating の薄ラッパ。デフォルト引数を埋める。"""
    h = {
        "horse_id": "test",
        "horse_name": "テスト馬",
        "horse_number": 5,
        "frame_number": 3,
        "popularity": 5,
        "running_style": "差し",
        "last_finishing_position": 1,
        "today_carry_weight": 56.0,
    }
    h.update(horse_overrides)
    return compute_horse_rating(
        **h,
        past_runs=past_runs or _empty_runs(),
        race_meta=race_meta or _meta(),
    )


def _rule_ids(result: HorseRating) -> set[str]:
    return {hit.rule_id for hit in result.matched}


# ==================================================================
# Phase 6.1 — 構造データ妥当性
# ==================================================================
def test_rating_rules_counts():
    """spec 通りの件数で各カテゴリが揃っていること。"""
    assert len(RATING_RULES_C) == 14, "C は 14 ルール"
    assert len(RATING_RULES_D) == 4, "D は 4 ルール"
    assert len(RATING_RULES_E) == 14, "E は 14 ルール"
    assert HONMEI_RATING_THRESHOLD == 100, "本命閾値は 100 点"


# ==================================================================
# C カテゴリ — 距離×馬場 上3F+通過順位改善
# ==================================================================
def test_C1_fires_on_turf_short_dry_under_threshold():
    """C1: 芝1400 良 33.2秒 + 通過順位改善(東京) → +50。"""
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.2, racecourse="東京",
        corners=(10, 5, 3, 2),
    )
    r = _compute(past_runs=[run] + _empty_runs(4),
                 race_meta=_meta(distance=1400, surface="芝", going="良"))
    assert "C1" in _rule_ids(r), f"C1 が発火するはず: {_rule_ids(r)}"
    assert r.total_rating >= 50, "C1 だけで +50 以上"


def test_C7_fires_on_turf_long_dry_with_corner():
    """C7: 芝3200 良 33.0秒 + 通過順位改善(京都) → +50。"""
    run = _run(
        surface="芝", distance=3200, going="良",
        last_3f=33.0, racecourse="京都",
        corners=(8, 6, 4, 2),
    )
    r = _compute(past_runs=[run] + _empty_runs(4),
                 race_meta=_meta(distance=3200, surface="芝", going="良"))
    assert "C7" in _rule_ids(r)


# ==================================================================
# D カテゴリ — 距離無関係 上3F+通過順位改善
# ==================================================================
def test_D1_fires_distance_agnostic_turf_dry():
    """D1: 芝 良 33.0秒 + 通過順位改善(任意距離)。距離が C1 と外れていても発火する。"""
    # 芝1500m(C1=≤1400 / C3=1600 のどちらにも該当しない距離)
    # 33.0秒は C1/C3 の閾値より小さいが、距離マッチしないので C は発火せず、D1 のみ。
    run = _run(
        surface="芝", distance=1500, going="良",
        last_3f=33.0, racecourse="東京",
        corners=(10, 5, 3, 2),
    )
    r = _compute(past_runs=[run] + _empty_runs(4),
                 race_meta=_meta(distance=1500))
    assert "D1" in _rule_ids(r), f"D1 が発火するはず: {_rule_ids(r)}"


# ==================================================================
# E カテゴリ — 距離×馬場 上3F のみ(通過順位改善 不要)
# ==================================================================
def test_E1_fires_without_corner_improvement():
    """E1: 芝1400 良 33.2秒 + 通過順位改善なし(corners 同一)→ E1 は発火、C1 は不発火。"""
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.2, racecourse="東京",
        corners=(5, 5, 5, 5),  # 改善なし
    )
    r = _compute(past_runs=[run] + _empty_runs(4),
                 race_meta=_meta(distance=1400))
    ids = _rule_ids(r)
    assert "C1" not in ids, "通過順位改善なしなので C1 は不発火"
    assert "E1" in ids, f"E1 は発火するはず: {ids}"


# ==================================================================
# C/D/E 排他処理 — 同一過去走で複数該当 → 最高 rate のみ
# ==================================================================
def test_cde_exclusion_prefers_C_over_D_and_E():
    """1 走で C1 (50)、D1 (20)、E1 (20) が全て該当 → STRICT で C1 のみ採用。"""
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.0, racecourse="東京",
        corners=(10, 5, 3, 2),
    )
    r = _compute(past_runs=[run] + _empty_runs(4),
                 race_meta=_meta(distance=1400))
    ids = _rule_ids(r)
    assert "C1" in ids, "C1 が選ばれる"
    assert "D1" not in ids, "STRICT で D1 は弾かれる"
    assert "E1" not in ids, "STRICT で E1 は弾かれる"
    assert r.total_rating == 50


def test_cde_exclusion_in_sum_all_keeps_all():
    """SUM_ALL ポリシーでは C/D/E 全部加算される。"""
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.0, racecourse="東京",
        corners=(10, 5, 3, 2),
    )
    r = compute_horse_rating(
        horse_id="test", horse_name="テ", horse_number=1, frame_number=1,
        popularity=1, running_style="差し", last_finishing_position=None,
        today_carry_weight=56.0,
        past_runs=[run] + _empty_runs(4),
        race_meta=_meta(distance=1400),
        policy=RatingPolicy.SUM_ALL,
    )
    ids = _rule_ids(r)
    assert {"C1", "D1", "E1"}.issubset(ids), f"SUM_ALL では全部発火: {ids}"
    assert r.total_rating == 50 + 20 + 20  # 90 点


# ==================================================================
# 同一 rule_id を複数走で発火 → dedup
# ==================================================================
def test_same_rule_id_dedup_across_runs():
    """C1 が 前走 と 3走前 で発火 → +50 一回のみ加算される。"""
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.2, racecourse="東京",
        corners=(10, 5, 3, 2),
    )
    # 5走分すべて同じ条件を入れる
    r = _compute(past_runs=[run] * 5,
                 race_meta=_meta(distance=1400))
    # C1 が発火しているはず、かつ +50 が 1 回だけ
    c1_hits = [h for h in r.matched if h.rule_id == "C1"]
    assert len(c1_hits) == 1, f"C1 dedup されるはず、got {len(c1_hits)}"


# ==================================================================
# F1 — ダート不良 + 逃げ → +30
# ==================================================================
def test_F1_dirt_heavy_nigeru():
    r = _compute(
        past_runs=_empty_runs(),
        race_meta=_meta(surface="ダ", going="不良", distance=1800),
        running_style="逃げ",
    )
    assert "F1" in _rule_ids(r)
    assert r.total_rating == 30


def test_F1_does_not_fire_on_dry_turf():
    r = _compute(
        past_runs=_empty_runs(),
        race_meta=_meta(surface="芝", going="良"),
        running_style="逃げ",
    )
    assert "F1" not in _rule_ids(r)


# ==================================================================
# F2 — 休養明け前走凡走 → 2,3走前で C/D/E 救済 + +15
# ==================================================================
def test_F2_rest_recovery_fires_when_2nd_3rd_match():
    """前走凡走 + 2,3走前で C 系発火 → F2 +15、本来の C5 等も加算。"""
    # 前走: 180日以上前、5着以下 (rule24 trigger)
    prev_run = _run(
        race_date="2026-04-01", surface="芝", distance=2000, going="良",
        last_3f=37.0, finishing_position=10, corners=(15, 14, 14, 13),
    )
    # 2走前: 評価対象、C5 (芝1800-2000m良 <34.0 + corner) 発火
    run_2 = _run(
        race_date="2025-09-01", surface="芝", distance=2000, going="良",
        last_3f=33.5, finishing_position=1, corners=(8, 6, 4, 2),
        racecourse="東京",
    )
    # 3走前: なんでもよい
    run_3 = _run(
        race_date="2025-06-01", surface="芝", distance=1800, going="良",
        last_3f=36.0, finishing_position=5, corners=(5, 5, 5, 5),
    )
    r = _compute(
        past_runs=[prev_run, run_2, run_3, None, None],
        race_meta=_meta(distance=2000, surface="芝", going="良"),
    )
    assert r.rule24_active, "rule24 (休養明け+前走凡走) が検出されること"
    assert "F2" in _rule_ids(r), f"F2 救済発火: {_rule_ids(r)}"
    assert "C5" in _rule_ids(r), f"2走前 C5 発火: {_rule_ids(r)}"


# ==================================================================
# F3 — 1600m+ + 斤量 -3kg(前走比) → +20
# ==================================================================
def test_F3_carry_weight_minus_3kg():
    """前走 58kg → 今回 55kg(-3kg)、距離 1800m → F3 発火。"""
    prev = _run(carry_weight=58.0, distance=2000, surface="芝", going="良")
    r = _compute(
        past_runs=[prev, None, None, None, None],
        race_meta=_meta(distance=1800),
        today_carry_weight=55.0,
    )
    assert "F3" in _rule_ids(r)
    assert any(hit.rate == 20 for hit in r.matched if hit.rule_id == "F3")


def test_F3_does_not_fire_under_1600m():
    """距離 1400m なら F3 は発火しない。"""
    prev = _run(carry_weight=58.0)
    r = _compute(
        past_runs=[prev, None, None, None, None],
        race_meta=_meta(distance=1400),
        today_carry_weight=55.0,
    )
    assert "F3" not in _rule_ids(r)


def test_F3_does_not_fire_when_carry_weight_increases():
    """斤量が増えていれば F3 は発火しない。"""
    prev = _run(carry_weight=55.0)
    r = _compute(
        past_runs=[prev, None, None, None, None],
        race_meta=_meta(distance=1800),
        today_carry_weight=58.0,
    )
    assert "F3" not in _rule_ids(r)


# ==================================================================
# 100 点閾値 — 本命確定 / 未到達は準◎ fallback
# ==================================================================
def test_100_threshold_crosses_to_main_pick():
    """rating 120 の馬が 1 頭、他は 0 → ◎本命確定。"""
    h_120 = _compute(
        past_runs=[
            _run(surface="芝", distance=1400, going="良", last_3f=33.0,
                 racecourse="東京", corners=(10,5,3,2)),
            _run(surface="芝", distance=1600, going="良", last_3f=34.0,
                 racecourse="東京", corners=(8,6,4,2)),
            _run(surface="芝", distance=1800, going="良", last_3f=33.5,
                 racecourse="東京", corners=(7,5,3,1)),
        ] + _empty_runs(2),
        race_meta=_meta(distance=1400),
        horse_id="strong", horse_name="強い馬", popularity=1,
    )
    h_low = _compute(
        past_runs=_empty_runs(),
        race_meta=_meta(distance=1400),
        horse_id="weak", horse_name="弱い馬", popularity=2,
    )
    # 強馬の rating を確認
    assert h_120.total_rating >= 100, f"強馬 rating: {h_120.total_rating}"
    j = determine_main_pick_v2([h_120, h_low], _meta(distance=1400))
    assert j.main_pick == "strong", f"strong が main_pick: {j.main_pick}"
    assert j.main_pick_marks >= 100


def test_under_100_falls_back_to_sub_pick():
    """誰も 100 点に届かないとき → 最高 rating の馬が準◎。"""
    h_50 = _compute(
        past_runs=[
            _run(surface="芝", distance=1400, going="良", last_3f=33.0,
                 racecourse="東京", corners=(10,5,3,2)),
        ] + _empty_runs(4),
        race_meta=_meta(distance=1400),
        horse_id="mid", horse_name="中位馬",
    )
    h_0 = _compute(past_runs=_empty_runs(), race_meta=_meta(distance=1400),
                   horse_id="weak", horse_name="弱馬")
    j = determine_main_pick_v2([h_50, h_0], _meta(distance=1400))
    assert j.main_pick is None, "誰も ≥100 ではない"
    assert j.sub_pick == "mid", f"最高 rating 馬が準◎: {j.sub_pick}"
    assert j.sub_pick_marks == 50


# ==================================================================
# 減点 B — ◎候補から除外
# ==================================================================
def test_B1_excludes_1st_pop_nigeru_from_main_pick():
    """1番人気で逃げ脚質の馬は rating 100+ でも◎候補から外れる。"""
    # 強い馬だが 1番人気の逃げ → B1 に該当
    strong_nigeru = _compute(
        past_runs=[
            _run(surface="芝", distance=1400, going="良", last_3f=33.0,
                 racecourse="東京", corners=(10,5,3,2)),
            _run(surface="芝", distance=1600, going="良", last_3f=34.0,
                 racecourse="東京", corners=(8,6,4,2)),
            _run(surface="芝", distance=1800, going="良", last_3f=33.5,
                 racecourse="東京", corners=(7,5,3,1)),
        ] + _empty_runs(2),
        race_meta=_meta(distance=1400),
        horse_id="lead", horse_name="逃げ馬", popularity=1, running_style="逃げ",
    )
    other = _compute(
        past_runs=[_run(surface="芝", distance=1400, going="良", last_3f=33.0,
                        racecourse="東京", corners=(10,5,3,2))] + _empty_runs(4),
        race_meta=_meta(distance=1400),
        horse_id="other", horse_name="他馬", popularity=3,
    )
    j = determine_main_pick_v2([strong_nigeru, other], _meta(distance=1400))
    # B1 適用後、strong_nigeru は除外される
    assert j.main_pick != "lead", "B1 該当馬が main_pick になっていない"
    assert any(d.rule_id == "B1" for d in j.demerit_entries)


def test_B2_excludes_hanshin_mile_outer_frame():
    """阪神1600m + 7枠 → B2 で◎除外。"""
    h = _compute(
        past_runs=[_run(surface="芝", distance=1600, going="良", last_3f=34.0,
                        racecourse="阪神", corners=(8,6,4,2))] + _empty_runs(4),
        race_meta=_meta(racecourse="阪神", distance=1600),
        horse_id="outer", horse_name="外枠馬", popularity=2, frame_number=7,
    )
    j = determine_main_pick_v2([h], _meta(racecourse="阪神", distance=1600))
    assert any(d.rule_id == "B2" for d in j.demerit_entries)


# ==================================================================
# ワイド候補 — A2-A5
# ==================================================================
def test_A3_previous_5th_marks_wide_candidate():
    """前走 5着 → A3 でワイド候補に。"""
    h_5 = _compute(
        past_runs=_empty_runs(),
        race_meta=_meta(),
        horse_id="x5", horse_name="X5",
        popularity=8, last_finishing_position=5,
    )
    fav = _compute(
        past_runs=_empty_runs(),
        race_meta=_meta(),
        horse_id="fav", horse_name="人気馬",
        popularity=1, frame_number=4,
    )
    wides = extract_wide_candidates_v2([h_5, fav], _meta())
    assert any(w.horse_id == "x5" for w in wides), "A3 でワイド候補化"
    matched = next(w for w in wides if w.horse_id == "x5").matched_rules
    assert "A3" in matched


# ==================================================================
# 単体実行用ランナー
# ==================================================================
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
