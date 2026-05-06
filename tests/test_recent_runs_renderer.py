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
from utils.recent_runs_renderer import _build_run_cell  # noqa: E402


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
