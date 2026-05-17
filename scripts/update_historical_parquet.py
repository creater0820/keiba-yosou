"""v1.10.0: TARGET SE CSV から既存 races.parquet を最新化する CLI スクリプト。

【使い方】
    # ドライラン(取り込まずサマリだけ確認)
    .venv/bin/python scripts/update_historical_parquet.py \
        --input data/test/target_history_sample.csv --dry-run

    # 本番実行(バックアップ付き)
    .venv/bin/python scripts/update_historical_parquet.py \
        --input data/test/target_history_sample.csv

    # ディレクトリ内の全 CSV をまとめて取り込み
    .venv/bin/python scripts/update_historical_parquet.py \
        --input data/test/

    # バックアップから復元(--restore <バックアップパス>)
    .venv/bin/python scripts/update_historical_parquet.py \
        --restore data/historical/races.parquet.bak.20260516_223600

【処理フロー】
  1. 既存 parquet を読み込み
  2. バックアップ作成(--no-backup 指定がない限り)
  3. 入力 CSV をパーサに通して DataFrame 化
  4. (race_id, horse_id) 複合キーで重複排除(既存優先)
  5. concat → race_date 降順ソート
  6. parquet に書き戻し
  7. サマリ出力

【既存 parquet 保護方針】
- バックアップは必ず作る(--no-backup は非推奨フラグ)
- dry-run なら parquet は一切変更しない
- 失敗時は元の parquet を保持
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils.target_history_parser import (  # noqa: E402
    PARQUET_COLUMNS,
    parse_se_csv,
)


DEFAULT_PARQUET = ROOT / "data" / "historical" / "races.parquet"
DEDUP_KEYS: tuple[str, ...] = ("race_id", "horse_id")


def _collect_csv_paths(input_path: Path) -> list[Path]:
    """ファイルなら 1 件、ディレクトリなら直下の .csv / .CSV を列挙。"""
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        out = sorted(
            list(input_path.glob("*.csv")) + list(input_path.glob("*.CSV"))
        )
        return [p for p in out if p.is_file()]
    raise FileNotFoundError(f"入力パスが見つかりません: {input_path}")


def _make_backup(parquet_path: Path) -> Path:
    """既存 parquet をタイムスタンプ付きでコピー。"""
    if not parquet_path.exists():
        raise FileNotFoundError(f"バックアップ元 parquet なし: {parquet_path}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = parquet_path.with_suffix(f".parquet.bak.{ts}")
    shutil.copy2(parquet_path, bak)
    return bak


def restore_from_backup(backup_path: Path, parquet_path: Path) -> None:
    """バックアップを本体 parquet に書き戻す。"""
    if not backup_path.exists():
        raise FileNotFoundError(f"バックアップが見つかりません: {backup_path}")
    shutil.copy2(backup_path, parquet_path)
    print(f"✓ {backup_path} → {parquet_path} に復元しました。")


def merge_dataframes(
    existing: pd.DataFrame,
    new_df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """既存 + 新規を (race_id, horse_id) 複合キーで dedup する。

    既存優先(=同一キーがあれば既存を残し、新規をスキップ)。
    race_date 降順でソートして返す。

    戻り値: (merged_df, summary_dict)
        summary_dict キー: existing_rows, new_input_rows,
                            new_unique_added, duplicates_skipped,
                            merged_rows
    """
    # 重複検出
    existing_keys = set(
        zip(existing["race_id"].astype(str), existing["horse_id"].astype(str))
    )
    new_df_keys = list(
        zip(new_df["race_id"].astype(str), new_df["horse_id"].astype(str))
    )
    keep_mask = pd.Series(
        [k not in existing_keys for k in new_df_keys],
        index=new_df.index,
    )
    new_unique = new_df[keep_mask].copy()
    dup_count = int((~keep_mask).sum())

    # concat
    merged = pd.concat(
        [existing, new_unique],
        ignore_index=True,
        sort=False,
    )

    # race_date 降順ソート(既存の運用慣習に合わせる)
    merged = merged.sort_values("race_date", ascending=False).reset_index(drop=True)

    summary = {
        "existing_rows":       int(len(existing)),
        "new_input_rows":      int(len(new_df)),
        "new_unique_added":    int(len(new_unique)),
        "duplicates_skipped":  int(dup_count),
        "merged_rows":         int(len(merged)),
    }
    return merged, summary


def update_parquet(
    csv_paths: list[Path],
    parquet_path: Path = DEFAULT_PARQUET,
    *,
    dry_run: bool = False,
    make_backup: bool = True,
) -> dict:
    """CSV 群を取り込んで parquet を更新する。"""
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"対象 parquet が存在しません: {parquet_path}\n"
            "data/historical/races.parquet をご確認ください。"
        )
    existing = pd.read_parquet(parquet_path)
    print(f"[既存 parquet] 行数 {len(existing):,} / "
          f"レース {existing['race_id'].nunique():,} / "
          f"馬 {existing['horse_id'].nunique():,} / "
          f"期間 {existing['race_date'].min()} 〜 {existing['race_date'].max()}")

    # 全 CSV を順次パース + 縦結合
    all_parsed: list[pd.DataFrame] = []
    per_file_summary: list[dict] = []
    for p in csv_paths:
        print(f"\n[パース] {p.name} ({p.stat().st_size / 1024 / 1024:.2f} MB)")
        result = parse_se_csv(p)
        print(f"  rows total={result.total_rows:,} parsed={result.parsed_rows:,} "
              f"skipped={result.skipped_rows:,}")
        print(f"  unique races={result.unique_races:,} horses={result.unique_horses:,} "
              f"期間 {result.date_min} 〜 {result.date_max}")
        if result.skipped_reasons:
            print(f"  skip 内訳: {result.skipped_reasons}")
        all_parsed.append(result.df)
        per_file_summary.append({
            "file":         p.name,
            "total":        result.total_rows,
            "parsed":       result.parsed_rows,
            "skipped":      result.skipped_rows,
            "unique_races": result.unique_races,
        })

    new_combined = pd.concat(all_parsed, ignore_index=True, sort=False)

    # 入力 CSV 内の重複も先に除去(複数日付・複数ファイルで重複しうる)
    before_dedup_in_input = len(new_combined)
    new_combined = new_combined.drop_duplicates(
        subset=list(DEDUP_KEYS), keep="first",
    ).reset_index(drop=True)
    dedup_in_input = before_dedup_in_input - len(new_combined)
    if dedup_in_input:
        print(f"\n[入力内 dedup] CSV 群内で {dedup_in_input:,} 件の重複を解消")

    # 既存 parquet と merge
    merged, summary = merge_dataframes(existing, new_combined)

    print("\n" + "=" * 64)
    print("【取り込みサマリ】")
    print("=" * 64)
    print(f"  既存行数        : {summary['existing_rows']:,}")
    print(f"  入力 CSV 行数   : {summary['new_input_rows']:,}")
    print(f"  新規追加        : {summary['new_unique_added']:,}")
    print(f"  重複スキップ    : {summary['duplicates_skipped']:,}")
    print(f"  最終行数        : {summary['merged_rows']:,}")
    print(f"  追加レース      : {merged['race_id'].nunique() - existing['race_id'].nunique():,}")
    print(f"  追加馬          : {merged['horse_id'].nunique() - existing['horse_id'].nunique():,}")
    print(f"  期間            : {merged['race_date'].min()} 〜 {merged['race_date'].max()}")

    if dry_run:
        print("\n[dry-run] parquet は変更されていません。")
        summary["dry_run"] = True
        summary["backup_path"] = None
        summary["per_file"] = per_file_summary
        return summary

    # バックアップ
    bak_path = None
    if make_backup:
        bak_path = _make_backup(parquet_path)
        print(f"\n✓ バックアップ作成: {bak_path}")

    # 列順序を既存 parquet と一致(安全策)
    merged = merged[list(PARQUET_COLUMNS)]

    # 書き戻し
    merged.to_parquet(parquet_path, index=False)
    print(f"✓ 更新完了: {parquet_path} ({parquet_path.stat().st_size / 1024 / 1024:.2f} MB)")

    summary["dry_run"] = False
    summary["backup_path"] = str(bak_path) if bak_path else None
    summary["per_file"] = per_file_summary
    return summary


def main():
    p = argparse.ArgumentParser(description="races.parquet 最新化(v1.10.0)")
    p.add_argument("--input", help="取り込み元 CSV ファイル or ディレクトリ")
    p.add_argument("--parquet", default=str(DEFAULT_PARQUET),
                    help=f"対象 parquet パス (既定: {DEFAULT_PARQUET})")
    p.add_argument("--dry-run", action="store_true",
                    help="取り込まずサマリのみ表示")
    p.add_argument("--no-backup", action="store_true",
                    help="バックアップを作らない(非推奨)")
    p.add_argument("--restore", help="バックアップから復元する場合のバックアップパス")
    args = p.parse_args()

    parquet_path = Path(args.parquet)

    if args.restore:
        restore_from_backup(Path(args.restore), parquet_path)
        return

    if not args.input:
        p.error("--input または --restore が必要です")

    csv_paths = _collect_csv_paths(Path(args.input))
    if not csv_paths:
        print(f"取り込み対象 CSV が見つかりません: {args.input}")
        sys.exit(1)
    print(f"取り込み対象 CSV {len(csv_paths)} 件:")
    for p_ in csv_paths:
        print(f"  - {p_}")

    update_parquet(
        csv_paths,
        parquet_path=parquet_path,
        dry_run=args.dry_run,
        make_backup=not args.no_backup,
    )


if __name__ == "__main__":
    main()
