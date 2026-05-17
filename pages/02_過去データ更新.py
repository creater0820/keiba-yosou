"""お父様が CLI を使わずに過去 parquet を最新化できる Streamlit ページ。

v1.10.0 で新規追加。複数 SE 形式 CSV をドラッグ&ドロップでアップロード →
バックアップ作成 → 重複排除 → parquet 書き戻し → 結果サマリ表示。

【保証事項】
- 既存の当日 CSV(DC / RA+SE)処理フローは完全不変
- 失敗時は parquet を破壊せずバックアップが残る
- 取り込み完了後に @st.cache_data.clear() で過去データキャッシュを無効化
  → サイドバーの「過去レース数」「登録馬数」が自動で新数値に
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

# 必ずプロジェクトルートを sys.path に追加(他ページと同じ慣習)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_historical_parquet import (
    DEFAULT_PARQUET,
    merge_dataframes,
    restore_from_backup,
    _make_backup,
)
from utils.target_history_parser import PARQUET_COLUMNS, parse_se_csv


st.set_page_config(
    page_title="過去データ更新",
    page_icon="🗂️",
    layout="wide",
)

st.title("🗂️ 過去データ更新(v1.10.0)")

st.markdown("""
TARGET frontier JV の **SE(成績)形式 CSV** をアップロードして、
過去レースデータを最新化します。

- アップロード前に既存 parquet のバックアップが自動作成されます
- 重複(同じレース × 同じ馬)は自動でスキップされます
- 完了後はサイドバーの「過去レース数」が自動更新されます
""")

st.divider()

# --- 現状サマリ ---
parquet_path = DEFAULT_PARQUET
if not parquet_path.exists():
    st.error(f"過去データが見つかりません: `{parquet_path}`")
    st.stop()

try:
    existing = pd.read_parquet(parquet_path)
except Exception as e:
    st.error(f"過去データの読み込みに失敗しました: {e}")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("過去レース数", f"{existing['race_id'].nunique():,}")
col2.metric("登録馬数", f"{existing['horse_id'].nunique():,}")
col3.metric("最古日付", str(existing["race_date"].min()))
col4.metric("最新日付", str(existing["race_date"].max()))

st.divider()

# --- CSV アップロード ---
st.subheader("📂 CSV をアップロード")

st.caption(
    "TARGET frontier JV の SE 形式 CSV(Shift_JIS、52 列、ヘッダなし)に対応。"
    "複数ファイルをまとめてアップロード可能。"
)

uploaded_files = st.file_uploader(
    "SE 形式 CSV をドラッグ&ドロップ",
    type=["csv", "CSV"],
    accept_multiple_files=True,
    key="se_csv_uploader",
)

mode_col1, mode_col2 = st.columns([1, 3])
with mode_col1:
    dry_run = st.checkbox(
        "🧪 ドライラン(取り込まずサマリのみ)", value=False,
    )
with mode_col2:
    make_backup = st.checkbox(
        "💾 バックアップを作る(推奨)", value=True,
    )

if uploaded_files:
    st.info(f"アップロード済み: {len(uploaded_files)} 件")
    for f in uploaded_files:
        st.caption(f"  - {f.name} ({len(f.getvalue()) / 1024 / 1024:.2f} MB)")

# --- 取り込み実行 ---
if st.button("🚀 取り込み実行", type="primary", disabled=not uploaded_files):
    all_parsed: list[pd.DataFrame] = []
    progress = st.progress(0.0, text="準備中...")
    log_box = st.container()

    with log_box:
        for i, f in enumerate(uploaded_files):
            progress.progress(
                (i + 0.3) / max(len(uploaded_files), 1),
                text=f"パース中 {i+1}/{len(uploaded_files)}: {f.name}",
            )
            try:
                result = parse_se_csv(f.getvalue())
            except Exception as e:
                st.error(
                    f"❌ {f.name} のパースに失敗: {type(e).__name__}: {e}"
                )
                continue

            st.write(
                f"✓ **{f.name}**: {result.parsed_rows:,} 行 / "
                f"{result.unique_races:,} レース / "
                f"{result.unique_horses:,} 馬 "
                f"({result.date_min} 〜 {result.date_max})"
            )
            if result.skipped_reasons:
                st.warning(f"  スキップ内訳: {result.skipped_reasons}")
            all_parsed.append(result.df)

    if not all_parsed:
        st.error("有効な CSV が 1 件もありませんでした。取り込みを中止します。")
        st.stop()

    new_combined = pd.concat(all_parsed, ignore_index=True, sort=False)
    new_combined = new_combined.drop_duplicates(
        subset=["race_id", "horse_id"], keep="first",
    ).reset_index(drop=True)

    progress.progress(0.7, text="既存 parquet と照合中...")
    merged, summary = merge_dataframes(existing, new_combined)

    st.divider()
    st.subheader("📊 取り込みサマリ")
    s1, s2, s3 = st.columns(3)
    s1.metric("新規追加", f"{summary['new_unique_added']:,}", f"+{summary['new_unique_added']:,}")
    s2.metric("重複スキップ", f"{summary['duplicates_skipped']:,}")
    s3.metric("最終行数", f"{summary['merged_rows']:,}",
              delta=f"+{summary['merged_rows'] - summary['existing_rows']:,}")

    new_race_count = merged["race_id"].nunique() - existing["race_id"].nunique()
    new_horse_count = merged["horse_id"].nunique() - existing["horse_id"].nunique()
    s4, s5, s6 = st.columns(3)
    s4.metric("追加レース", f"+{new_race_count:,}")
    s5.metric("追加馬", f"+{new_horse_count:,}")
    s6.metric("期間", f"{merged['race_date'].max()}",
              delta=f"〜 +{(pd.Timestamp(merged['race_date'].max()) - pd.Timestamp(existing['race_date'].max())).days} 日")

    if dry_run:
        st.info("🧪 ドライランのため parquet は変更されていません。")
        progress.progress(1.0, text="完了(ドライラン)")
        st.stop()

    # 実書き込み
    progress.progress(0.85, text="バックアップ作成中...")
    bak_path = None
    if make_backup:
        try:
            bak_path = _make_backup(parquet_path)
            st.success(f"💾 バックアップ作成: `{bak_path.name}`")
        except Exception as e:
            st.error(f"バックアップ失敗、取り込みを中止: {e}")
            st.stop()

    progress.progress(0.95, text="parquet 書き込み中...")
    try:
        merged_ordered = merged[list(PARQUET_COLUMNS)]
        merged_ordered.to_parquet(parquet_path, index=False)
    except Exception as e:
        st.error(f"❌ parquet 書き込み失敗: {e}")
        if bak_path:
            st.warning(f"バックアップ `{bak_path}` から手動で復元してください。")
        st.stop()

    progress.progress(1.0, text="完了")

    # 過去データキャッシュを無効化(サイドバー / get_historical の再読込)
    st.cache_data.clear()
    st.success(
        f"✅ 更新完了。新規 {summary['new_unique_added']:,} 行追加、"
        f"重複 {summary['duplicates_skipped']:,} 件スキップ。"
        " サイドバーの過去データ表示も自動更新されます。"
    )
    st.balloons()

st.divider()

# --- バックアップ管理 ---
st.subheader("💾 バックアップ管理")

backup_pattern = f"{parquet_path.stem}.parquet.bak.*"
backups = sorted(parquet_path.parent.glob(backup_pattern), reverse=True)

if not backups:
    st.caption("バックアップは現在ありません。")
else:
    st.caption(f"バックアップ {len(backups)} 件(新しい順):")
    for b in backups[:10]:  # 最大 10 件表示
        size_mb = b.stat().st_size / 1024 / 1024
        col_a, col_b = st.columns([4, 1])
        with col_a:
            st.code(f"{b.name} ({size_mb:.2f} MB)")
        with col_b:
            if st.button("⏪ 復元", key=f"restore_{b.name}"):
                try:
                    restore_from_backup(b, parquet_path)
                    st.cache_data.clear()
                    st.success(f"✓ `{b.name}` から復元しました。")
                    st.rerun()
                except Exception as e:
                    st.error(f"復元失敗: {e}")

st.divider()

with st.expander("📖 運用ガイド(お父様向け)"):
    st.markdown("""
    ### TARGET frontier JV から CSV を出す手順
    1. TARGET frontier JV を起動
    2. **「成績」または「SE(過去成績)」エクスポート**メニューを開く
    3. 期間指定:前回更新日 〜 本日
    4. 形式オプション:
       - 文字コード = **Shift_JIS**
       - ヘッダ行 = **無し**(ある場合も自動判別はするが、できれば無しが安定)
    5. CSV ファイルとして保存(ファイル名は任意)

    ### このページでの取り込み手順
    1. 上の「📂 CSV をアップロード」へファイルをドラッグ&ドロップ(複数同時 OK)
    2. **初回は「🧪 ドライラン」**にチェックを入れて、追加件数を先に確認する
    3. 件数が想定通りなら、ドライランのチェックを外して再度「🚀 取り込み実行」
    4. 「💾 バックアップを作る」は **常に ON のまま**にしてください
    5. サイドバーの「過去レース数」が増えていれば成功

    ### おかしくなったとき
    - 上の「💾 バックアップ管理」から **「⏪ 復元」** ボタンをクリック
    - 前回の状態に 1 クリックで戻せます

    ### 取り込み頻度の目安
    - 月 1 回 程度。**前回取り込み以降のレース** だけ出力すれば十分です。
    - 古いレースを含めて出しても、重複は自動でスキップされます。
    """)
