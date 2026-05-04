"""
的中履歴ダッシュボード(Streamlit マルチページ)。

このファイルは pages/ に置くことで Streamlit が自動的にサイドバー
ナビゲーションへ追加する。ファイル名先頭の "02_" はナビ表示順制御用。

機能:
- predictions/*.json と data/historical/races.parquet を突合
- 直近30日 / 全期間 のメトリクス
- 印別パフォーマンス表
- 競馬場 × 距離帯 のヒートマップ
- 直近10レースの予想 vs 結果テーブル
- 「最新の過去データで突合を再実行」ボタン
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from data_loader import load_historical_data
from utils.hit_matching import (
    HIT_HISTORY_PATH,
    match_predictions_to_results,
    per_mark_performance,
    race_summary,
    save_hit_history,
)
from utils.prediction_io import load_predictions

# =====================================================================
# ページ設定
# =====================================================================
st.set_page_config(page_title="的中履歴ダッシュボード", page_icon="📊", layout="wide")

st.title("📊 的中履歴ダッシュボード")
st.caption("予想 (predictions/*.json) と実結果 (races.parquet) の突合結果")


# =====================================================================
# データ読み込み(キャッシュ済み)
# =====================================================================
# キャッシュ世代タグ。突合ロジックの仕様変更時に上げる。
HIT_HISTORY_SCHEMA_VERSION = "v1-initial"


@st.cache_data(show_spinner="予想と過去データを読み込み中…")
def _load_matched(_schema_version: str = HIT_HISTORY_SCHEMA_VERSION) -> pd.DataFrame:
    """predictions × races を突合して返す。"""
    predictions = load_predictions()
    if predictions.empty:
        return pd.DataFrame()
    historical = load_historical_data()
    return match_predictions_to_results(predictions, historical.races)


# =====================================================================
# トップバー: 期間切り替え + 再突合ボタン
# =====================================================================
with st.container():
    bar_col1, bar_col2, bar_col3 = st.columns([2, 2, 1])

    with bar_col1:
        period_choice = st.radio(
            "対象期間",
            options=["全期間", "直近30日"],
            horizontal=True,
            label_visibility="collapsed",
        )
    with bar_col2:
        st.caption(f"突合履歴: {'有り' if HIT_HISTORY_PATH.exists() else '未生成'}")
    with bar_col3:
        if st.button("🔄 再突合実行", help="最新の過去データで突合を再実行し、hit_history を更新します。"):
            st.cache_data.clear()
            try:
                m = _load_matched()
                if not m.empty:
                    save_hit_history(m)
                    st.success(f"再突合完了({len(m)} 行)")
                else:
                    st.warning("predictions/ が空です。")
            except FileNotFoundError as e:
                st.error(str(e))
            st.rerun()

st.divider()

# =====================================================================
# 突合結果のロード
# =====================================================================
try:
    matched = _load_matched()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

if matched.empty:
    st.warning(
        "📭 **predictions/ に予想JSONがまだありません。**\n\n"
        "サンプルデータを入れる場合: `python scripts/seed_test_predictions.py` を実行してください。\n"
        "本番運用では、メインページで予想実行 → 「💾 予想を保存」→ ZIP を解凍して "
        "`predictions/` にコミット、というフローでデータが溜まります。"
    )
    st.stop()


# =====================================================================
# 期間フィルタ
# =====================================================================
# race_date が文字列のことも datetime のこともあるので両対応で正規化
matched["race_date"] = pd.to_datetime(matched["race_date"], errors="coerce")
if period_choice == "直近30日" and matched["race_date"].notna().any():
    cutoff = matched["race_date"].max() - timedelta(days=30)
    filtered = matched[matched["race_date"] >= cutoff].copy()
else:
    filtered = matched.copy()

# 結果が出ていない予想(未確定)は分母から除外したいので分けて持つ
resolved = filtered[filtered["is_resolved"]].copy()
unresolved = filtered[~filtered["is_resolved"]].copy()


# =====================================================================
# トップメトリクス(◎単勝/複勝、◎○いずれか連対、三連複)
# =====================================================================
race_sum = race_summary(resolved)

n_total_races = filtered["race_id"].nunique()
n_resolved_races = resolved["race_id"].nunique()
n_unresolved_races = unresolved["race_id"].nunique()


def _safe_rate(numer: float, denom: float) -> str:
    return f"{numer/denom:.1%}" if denom > 0 else "—"


# ◎ の単勝/複勝(分母は ◎ 印を付けたレース数)
honmei_rows = resolved[resolved["mark"] == "◎"]
n_honmei = len(honmei_rows)
honmei_win = int((honmei_rows["actual_finishing_position"] == 1).sum())
honmei_show = int((honmei_rows["actual_finishing_position"] <= 3).sum())

# ◎○ いずれか2着以内
def _has_top2_in_marks(group: pd.DataFrame, marks: list[str]) -> bool:
    sub = group[group["mark"].isin(marks)]
    return bool((sub["actual_finishing_position"] <= 2).fillna(False).any())


per_race_resolved = (
    resolved.groupby("race_id").apply(
        lambda g: _has_top2_in_marks(g, ["◎", "○"]), include_groups=False
    )
    if len(resolved) > 0
    else pd.Series(dtype=bool)
)
honmaru_or_taikou_top2 = int(per_race_resolved.sum()) if len(per_race_resolved) > 0 else 0

# 三連複(◎○▲ のうち2頭以上が3着以内)
sanrenpuku_hit = int(race_sum["hit_2head_in_top3"].fillna(0).astype(int).sum()) if not race_sum.empty else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("予想総レース数", f"{n_total_races}", help=f"うち未確定 {n_unresolved_races}")
c2.metric("◎ 単勝的中率", _safe_rate(honmei_win, n_honmei), help=f"{honmei_win} / {n_honmei}")
c3.metric("◎ 複勝率", _safe_rate(honmei_show, n_honmei), help=f"{honmei_show} / {n_honmei}")
c4.metric("◎○ 連対率", _safe_rate(honmaru_or_taikou_top2, n_resolved_races),
          help=f"{honmaru_or_taikou_top2} / {n_resolved_races}\n◎または○のいずれかが2着以内に入ったレース")
c5.metric("三連複的中率", _safe_rate(sanrenpuku_hit, n_resolved_races),
          help=f"{sanrenpuku_hit} / {n_resolved_races}\n◎○▲のうち2頭以上が3着以内")

if n_unresolved_races > 0:
    st.info(f"※ 未確定レース {n_unresolved_races} 件は的中率の分母から除外しています。")


# =====================================================================
# 印別パフォーマンス表
# =====================================================================
st.subheader("印別パフォーマンス")
mark_perf = per_mark_performance(resolved)
if not mark_perf.empty:
    display = mark_perf.copy()
    display["win_rate"] = display["win_rate"].apply(lambda x: f"{x:.1%}")
    display["place_rate"] = display["place_rate"].apply(lambda x: f"{x:.1%}")
    display["show_rate"] = display["show_rate"].apply(lambda x: f"{x:.1%}")
    display = display.rename(columns={
        "mark": "印", "n": "出現数", "wins": "1着",
        "places": "2着内延べ", "shows": "3着内延べ",
        "win_rate": "単勝率", "place_rate": "連対率", "show_rate": "複勝率",
    })
    st.dataframe(display, hide_index=True, use_container_width=True)
else:
    st.write("(データなし)")


# =====================================================================
# 競馬場 × 距離帯 ヒートマップ(◎の複勝率)
# =====================================================================
st.subheader("競馬場 × 距離帯 ヒートマップ(◎ の複勝率)")
honmei_resolved = resolved[resolved["mark"] == "◎"].copy()
if not honmei_resolved.empty:
    # 距離帯を 200m 単位でビニング
    honmei_resolved["distance_band"] = pd.cut(
        honmei_resolved["distance"],
        bins=[0, 1200, 1400, 1600, 1800, 2000, 2400, 4000],
        labels=["〜1200m", "1300-1400m", "1500-1600m", "1700-1800m",
                "1900-2000m", "2100-2400m", "2500m〜"],
        include_lowest=True,
    )
    pivot = (
        honmei_resolved.assign(hit=lambda d: (d["actual_finishing_position"] <= 3).astype(int))
        .pivot_table(index="racecourse", columns="distance_band", values="hit",
                     aggfunc="mean", observed=False)
    )
    if not pivot.empty:
        st.dataframe(
            pivot.style.format("{:.1%}", na_rep="—").background_gradient(cmap="YlGn", axis=None),
            use_container_width=True,
        )
    else:
        st.caption("十分なデータがありません。")
else:
    st.caption("◎ の予想がまだありません。")


# =====================================================================
# 直近10レース 予想 vs 結果
# =====================================================================
st.subheader("直近 10 レース 予想 vs 結果")
recent_summary = race_summary(filtered).sort_values("race_date", ascending=False).head(10)
if not recent_summary.empty:
    show = recent_summary.copy()
    show["race_date"] = pd.to_datetime(show["race_date"]).dt.strftime("%Y-%m-%d")
    show["着順"] = show["honmei_finishing_position"].apply(
        lambda x: "未確定" if pd.isna(x) else f"{int(x)}着"
    )
    show["的中"] = show.apply(
        lambda r: ("—" if pd.isna(r["hit_honmei_show"])
                   else ("✅複勝" if r["hit_honmei_show"] == 1 else "❌")),
        axis=1,
    )
    # 表示列の整理
    show = show.rename(columns={
        "race_date": "日付", "racecourse": "場", "race_number": "R",
        "race_name": "レース名", "honmei_horse_name": "◎",
    })[["日付", "場", "R", "レース名", "◎", "着順", "的中"]]
    st.dataframe(show, hide_index=True, use_container_width=True)
else:
    st.caption("予想データがありません。")


# =====================================================================
# サイドバー: データ概要
# =====================================================================
with st.sidebar:
    st.subheader("📁 データ概要")
    st.metric("予想総レース数", f"{matched['race_id'].nunique()}")
    st.metric("延べ推奨馬数", f"{len(matched)}")
    st.metric("結果未確定", f"{matched[~matched['is_resolved']]['race_id'].nunique()}")
    st.divider()
    st.caption("ロジック世代別")
    if "logic_version" in matched.columns:
        version_counts = matched.drop_duplicates("race_id").groupby("logic_version").size()
        for v, n in version_counts.items():
            st.write(f"- `{v}`: {n} レース")
