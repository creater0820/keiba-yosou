"""
直近5走戦歴マトリクスのセル組み立て(_build_run_cell)のユニットテスト。

主な検証対象:
- 上がり3F の緑文字は「○ルール (R9〜R22) が 1 本でも該当する走か」で決まる。
  単純な 33.5秒 閾値ではない(旧仕様 AGARI_THRESHOLD は廃止)。
- 距離完全一致 ★ は別タスクなので変更しない(下位互換チェックも兼ねる)。

実行:
- python tests/test_recent_runs_renderer.py     # 単体実行(pytest 不要)
- python -m pytest tests/test_recent_runs_renderer.py -v  # pytest 経由
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.onmark_rules import matches_any_onmark_rule  # noqa: E402
from utils.recent_runs_renderer import (  # noqa: E402
    _build_run_cell,
    _format_course_with_track,
    _format_horse_label,
    _format_pass_order,
    _is_blank_jockey,
    _is_exact_distance_match,
    _is_jockey_changed,
)


# ==================================================================
# 共通: 過去走 dict の組み立てヘルパ
# ==================================================================
def _run(
    *,
    surface: str = "芝",
    distance: int = 1400,
    going: str = "良",
    last_3f: float | None = 33.0,
    racecourse: str = "東京",
    finishing_position: int = 1,
    corners: tuple[int, ...] = (10, 5, 3, 2),
) -> dict:
    """通過順位改善あり(初角>4角)の好走サンプルを 1 件作る。"""
    d = {
        "surface": surface,
        "distance": distance,
        "going": going,
        "last_3f": last_3f,
        "racecourse": racecourse,
        "finishing_position": finishing_position,
    }
    for i, c in enumerate(corners, start=1):
        d[f"corner_{i}"] = c
    return d


def _has_pass_class(html_cell: str) -> bool:
    """セル HTML に last3f-pass(緑強調)クラスが付いているか。"""
    return "last3f-pass" in html_cell


# ==================================================================
# Case 1: 芝1400 良 33.2秒 + 通過順位改善 + 東京 → R9 発火 → 緑
# ==================================================================
def test_case1_turf1400_good_33_2_tokyo_fires_r9():
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.2, racecourse="東京",
    )
    is_pass, matched = matches_any_onmark_rule(run)
    assert is_pass and "R9" in matched, f"R9 should fire, got matched={matched}"

    cell = _build_run_cell(run, target_surface="芝", target_distance=1400)
    assert _has_pass_class(cell), "緑(last3f-pass)が付くべき"
    assert 'title="R9 該当"' in cell, "tooltip に R9 が出るべき"


# ==================================================================
# Case 2: 芝1400 良 33.4秒 + 通過順位改善 + 中山 → R9 阪神中山特例 → 緑
# ==================================================================
def test_case2_turf1400_good_33_4_nakayama_fires_r9_special():
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.4, racecourse="中山",
    )
    is_pass, matched = matches_any_onmark_rule(run)
    assert is_pass and "R9" in matched, f"R9 特例 (33.5) で発火すべき: {matched}"

    cell = _build_run_cell(run, target_surface="芝", target_distance=1400)
    assert _has_pass_class(cell), "中山特例で緑(last3f-pass)が付くべき"


# ==================================================================
# Case 3: 芝1400 良 33.4秒 + 通過順位改善 + 東京 → R9 不発(33.3 必要) → 通常
# ==================================================================
def test_case3_turf1400_good_33_4_tokyo_does_not_fire_r9():
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=33.4, racecourse="東京",
    )
    is_pass, matched = matches_any_onmark_rule(run)
    assert not is_pass, (
        f"東京での 33.4秒 は R9(<33.3 必要)に該当しないはずだが matched={matched}"
    )

    cell = _build_run_cell(run, target_surface="芝", target_distance=1400)
    assert not _has_pass_class(cell), "緑が付いてはいけない"
    assert 'title=' not in cell, "tooltip も出ないはず"


# ==================================================================
# Case 4: 芝3200 良 33.0秒 + 通過順位改善 → R15 発火(<35.0) → 緑
# ==================================================================
def test_case4_turf3200_good_33_0_fires_r15():
    run = _run(
        surface="芝", distance=3200, going="良",
        last_3f=33.0, racecourse="京都",  # 京都は阪神中山特例の対象外
    )
    is_pass, matched = matches_any_onmark_rule(run)
    assert is_pass and "R15" in matched, f"R15 が発火すべき: {matched}"

    cell = _build_run_cell(run, target_surface="芝", target_distance=3200)
    assert _has_pass_class(cell)
    assert "R15" in cell, "tooltip 文字列に R15 が含まれるはず"


# ==================================================================
# Case 5.5: CSS specificity リグレッション(本番事故再発防止)
#
# .recent-runs-matrix .run-cell .last3f       (0,3,0) color: rgba(255,255,255,0.7)
# が定義されているので、緑側もそれ以上の specificity が必要。
#
# 過去事故: `.recent-runs-matrix .last3f-pass` (0,2,0) で書かれていたため、
# クラスは付くのに色が負けて本番で全く緑にならなかった(commit 489028c で出荷
# された不具合)。再発防止のため CSS 文字列を直接 assert する。
# ==================================================================
def test_css_specificity_is_3_levels_for_pass_class():
    from utils.recent_runs_renderer import _MATRIX_CSS

    good = ".recent-runs-matrix .run-cell .last3f-pass"
    # 過去のバグった書き方(これだけが残っていてはダメ — .last3f に負ける)
    bad_only = ".recent-runs-matrix .last3f-pass {"

    assert good in _MATRIX_CSS, (
        "緑強調セレクタは .run-cell を挟んだ 3 階層 (specificity 0,3,0) で "
        "書かれている必要がある(.last3f の color: rgba(255,255,255,0.7) "
        "に勝つため)"
    )
    # ↑ "good" を含み、かつ ".recent-runs-matrix .last3f-pass {" 単独の
    # ルールがあってはならない(あるとしたら旧バグった書き方の残骸)
    # `.recent-runs-matrix .run-cell .last3f-pass` は部分文字列として
    # `.recent-runs-matrix .last3f-pass` を含まないので素朴な `in` 比較で OK
    # ではないことに注意 → 検査は { まで含めた "bad_only" と一致しないこと
    bare_pattern_count = _MATRIX_CSS.count(bad_only)
    assert bare_pattern_count == 0, (
        f"古い `.recent-runs-matrix .last3f-pass {{` 単独セレクタが "
        f"残っている({bare_pattern_count} 箇所)— 必ず .run-cell を挟む形に揃える"
    )


# ==================================================================
# 場名フォーマッタ _format_course_with_track
# ==================================================================
def test_course_with_track_full_data():
    s = _format_course_with_track("芝", 2400, "東京")
    assert s == "芝2400（東京）", f"got {s!r}"
    # 全角括弧 (U+FF08 / U+FF09) であること、半角 (U+0028 / U+0029) は使わない
    assert "（" in s and "）" in s, f"全角括弧が見つからない: {s!r}"
    assert "(" not in s and ")" not in s, f"半角括弧が混入: {s!r}"


def test_course_with_track_dirt():
    assert _format_course_with_track("ダ", 1800, "阪神") == "ダ1800（阪神）"


def test_course_with_track_no_racecourse():
    """場名が空文字なら括弧自体を出さない。"""
    assert _format_course_with_track("芝", 1600, "") == "芝1600"


def test_course_with_track_all_missing():
    """サーフェス・距離が両方欠損なら ── を返す。"""
    assert _format_course_with_track("", 0, "東京") == "──"


# ==================================================================
# 通過順フォーマッタ _format_pass_order
# ==================================================================
def test_pass_order_all_4_corners():
    assert _format_pass_order(
        {"corner_1": 2, "corner_2": 1, "corner_3": 5, "corner_4": 6}
    ) == "2-1-5-6"


def test_pass_order_short_distance_only_3rd_4th():
    """短距離レースで corner_1,2 が無い場合、有効分のみ詰める。"""
    assert _format_pass_order(
        {"corner_1": None, "corner_2": None, "corner_3": 5, "corner_4": 6}
    ) == "5-6"


def test_pass_order_all_zero_returns_empty():
    """全部 0(障害競走等で記録無し)は空文字。"""
    assert _format_pass_order(
        {"corner_1": 0, "corner_2": 0, "corner_3": 0, "corner_4": 0}
    ) == ""


def test_pass_order_with_nan_skipped():
    """NaN はスキップして詰める。"""
    assert _format_pass_order(
        {"corner_1": float("nan"), "corner_2": 3, "corner_3": 2, "corner_4": 1}
    ) == "3-2-1"


def test_pass_order_empty_dict():
    """corner キーすら無ければ空文字を返す(例外なし)。"""
    assert _format_pass_order({}) == ""


# ==================================================================
# ★ サーフェス + 距離 完全一致(芝1200 と ダ1200 を区別する)
# ==================================================================
def test_star_requires_both_distance_and_surface_match():
    # 同サーフェス + 同距離 → True
    assert _is_exact_distance_match(1200, "芝", 1200, "芝") is True
    # サーフェス違い → False(芝1200 ≠ ダ1200)
    assert _is_exact_distance_match(1200, "ダ", 1200, "芝") is False
    # 距離違い → False
    assert _is_exact_distance_match(1400, "芝", 1200, "芝") is False
    # サーフェス欠損 → False
    assert _is_exact_distance_match(1200, "", 1200, "芝") is False


# ==================================================================
# 統合: _build_run_cell が場名・通過順・★ を含む期待形を出す
# ==================================================================
def test_build_run_cell_renders_track_and_pass_order():
    run = _run(
        surface="ダ", distance=1800, going="良", last_3f=38.0,
        racecourse="阪神", finishing_position=6,
        corners=(2, 1, 5, 6),
    )
    html_out = _build_run_cell(run, target_surface="ダ", target_distance=1800)
    # 場名併記(全角括弧)
    assert "ダ1800（阪神）" in html_out
    # 通過順併記
    assert '<span class="pass-order">2-1-5-6</span>' in html_out
    # ★ サーフェス+距離一致で行頭マーカー
    assert 'distance-match-star' in html_out


def test_build_run_cell_omits_pass_order_when_no_corner_data():
    """通過順データが無い走では <span class="pass-order"> 自体を出さない。"""
    run = _run(
        surface="芝", distance=1600, going="良", last_3f=34.5,
        racecourse="東京", corners=(),  # コーナー全欠損
    )
    html_out = _build_run_cell(run, target_surface="芝", target_distance=1600)
    assert 'pass-order' not in html_out, (
        "コーナー全欠損では pass-order span 自体を出さない(──ではなく非表示)"
    )


# ==================================================================
# Case 6: last_3f が None → 例外なし、通常表示、緑も付かない
# ==================================================================
def test_case5_null_last3f_no_exception_no_green():
    run = _run(
        surface="芝", distance=1400, going="良",
        last_3f=None, racecourse="東京",
    )
    # 例外を出さない
    cell = _build_run_cell(run, target_surface="芝", target_distance=1400)
    assert "──" in cell, "上3F は ── で表示されるはず"
    assert not _has_pass_class(cell), "null だから緑はつかない"


# ==================================================================
# T1: ジョッキー変更検出
# ==================================================================
def test_jockey_changed_when_different():
    assert _is_jockey_changed("武豊", "ルメール") is True


def test_jockey_changed_false_when_same():
    assert _is_jockey_changed("武豊", "武豊") is False


def test_jockey_changed_strips_whitespace():
    """前後空白だけの違いは「同じ」と判定するべき。"""
    assert _is_jockey_changed(" 武豊 ", "武豊") is False


def test_jockey_changed_skipped_when_either_blank():
    """どちらかが欠損なら判定スキップ(False を返す)。"""
    assert _is_jockey_changed(None, "武豊") is False
    assert _is_jockey_changed("武豊", None) is False
    assert _is_jockey_changed("", "武豊") is False
    assert _is_jockey_changed("武豊", "") is False


# ==================================================================
# T2: 馬名ラベルへのジョッキー追加(末尾「(jockey)」+ 赤字フラグ)
# ==================================================================
def test_horse_label_appends_today_jockey():
    label = _format_horse_label("◎", 7, "クロワデュノール", today_jockey="北村友一")
    assert "クロワデュノール" in label
    assert "北村友一" in label
    assert 'class="jockey-today"' in label
    assert "jockey-changed" not in label, "同じジョッキーなら赤字クラス無し"


def test_horse_label_appends_jockey_changed_class():
    label = _format_horse_label(
        "◎", 7, "クロワデュノール",
        today_jockey="武豊", jockey_changed=True,
    )
    assert 'jockey-changed' in label
    assert "武豊" in label


def test_horse_label_unknown_when_blank_jockey():
    label = _format_horse_label("◎", 7, "X", today_jockey="")
    # 二重括弧 「((不明))」 にならず 「(不明)」 になる
    assert "(不明)" in label
    assert "((" not in label, f"二重括弧バグ: {label!r}"


def test_horse_label_no_jockey_section_when_param_omitted():
    """today_jockey=None で渡した場合は jockey 部分を一切出さない。"""
    label = _format_horse_label("◎", 7, "X", today_jockey=None)
    assert "jockey-today" not in label


# ==================================================================
# T3: 過去走セルにジョッキー 4 行目が含まれる
# ==================================================================
def test_run_cell_includes_jockey_row():
    run = _run(
        surface="芝", distance=1600, going="良",
        last_3f=34.0, racecourse="東京", corners=(8, 6, 4, 2),
    )
    run["jockey"] = "ルメール"
    cell = _build_run_cell(run, target_surface="芝", target_distance=1600)
    assert '<div class="jockey">ルメール</div>' in cell


# ==================================================================
# T4: 過去走で jockey 欠損 → 「(不明)」表示で例外を出さない
# ==================================================================
def test_run_cell_jockey_missing_shows_unknown():
    run_none = _run(
        surface="芝", distance=1600, going="良",
        last_3f=34.0, racecourse="東京", corners=(8, 6, 4, 2),
    )
    run_none["jockey"] = None
    cell = _build_run_cell(run_none, target_surface="芝", target_distance=1600)
    assert '<div class="jockey">(不明)</div>' in cell

    run_blank = dict(run_none, jockey="")
    cell2 = _build_run_cell(run_blank, target_surface="芝", target_distance=1600)
    assert '<div class="jockey">(不明)</div>' in cell2


# ==================================================================
# T5: past_runs[0] = None のとき jockey-changed 判定スキップ
# ==================================================================
def test_jockey_changed_skipped_when_no_prev_run():
    """前走 dict が None なら jockey の比較は走らせない(初出走馬対応)。"""
    # _is_jockey_changed への入力で「前走 jockey」が None なら False
    assert _is_jockey_changed("武豊", None) is False
    # _is_blank_jockey でも検証
    assert _is_blank_jockey(None) is True
    assert _is_blank_jockey(float("nan")) is True
    assert _is_blank_jockey("") is True
    assert _is_blank_jockey("(不明)") is True
    assert _is_blank_jockey("武豊") is False


# ==================================================================
# 単体実行用ランナー(他テストと同じパターン)
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
