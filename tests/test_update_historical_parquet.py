"""v1.10.0: scripts/update_historical_parquet.py の主要関数テスト。

dedup(複合キー)・dataframe merge・バックアップ作成のロジックを単体検証。
実 parquet を破壊しないよう、一時 parquet で完結する。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from scripts.update_historical_parquet import (  # noqa: E402
    DEDUP_KEYS,
    _make_backup,
    merge_dataframes,
    restore_from_backup,
    update_parquet,
)
from utils.target_history_parser import PARQUET_COLUMNS  # noqa: E402


def _make_row(race_id: str, horse_id: str, **overrides) -> dict:
    """既存 parquet スキーマ準拠の 1 行 dict を生成。"""
    base = {
        "race_id":            race_id,
        "race_date":          "2025-07-26",
        "racecourse":         "東京",
        "race_number":        1,
        "race_name":          "テスト",
        "post_time":          "",
        "distance":           1600,
        "surface":            "芝",
        "going":              "良",
        "finishing_position": 1,
        "horse_number":       1,
        "horse_id":           horse_id,
        "horse_name":         "テスト馬",
        "jockey":             "騎手",
        "trainer":            "調教師",
        "weight":             500,
        "carry_weight":       55.0,
        "weight_change":      0,
        "time":               "1:32.0",
        "last_3f":            33.5,
        "popularity":         1,
        "odds":               1.5,
        "corner_1":           pd.NA,
        "corner_2":           pd.NA,
        "corner_3":           1,
        "corner_4":           1,
    }
    base.update(overrides)
    return base


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """dtype を既存 parquet に合わせた DataFrame を返す。"""
    df = pd.DataFrame(rows, columns=list(PARQUET_COLUMNS))
    int_cols = (
        "race_number", "distance", "finishing_position", "horse_number",
        "weight", "popularity", "corner_1", "corner_2", "corner_3", "corner_4",
    )
    for c in int_cols:
        df[c] = pd.array(df[c].tolist(), dtype="Int64")
    for c in ("carry_weight", "last_3f", "odds"):
        df[c] = df[c].astype("float64")
    df["weight_change"] = df["weight_change"].astype("int64")
    return df


# ==================================================================
# merge_dataframes
# ==================================================================
def test_merge_no_overlap_adds_all():
    """重複なし → 全件追加。"""
    existing = _make_df([_make_row("R20230101-東01", "00000001")])
    new_df = _make_df([_make_row("R20260510-新01", "00000002")])
    merged, summary = merge_dataframes(existing, new_df)
    assert summary["new_unique_added"] == 1
    assert summary["duplicates_skipped"] == 0
    assert summary["merged_rows"] == 2


def test_merge_full_overlap_skips_all():
    """全件重複 → 1 件も追加されない。"""
    row = _make_row("R20230101-東01", "00000001")
    existing = _make_df([row])
    new_df = _make_df([row])
    merged, summary = merge_dataframes(existing, new_df)
    assert summary["new_unique_added"] == 0
    assert summary["duplicates_skipped"] == 1
    assert summary["merged_rows"] == 1


def test_merge_partial_overlap_dedups_correctly():
    """部分重複 → 重複だけスキップ、新規は追加。"""
    r1 = _make_row("R20230101-東01", "00000001")
    r2 = _make_row("R20260510-新01", "00000002")
    r3 = _make_row("R20260510-新01", "00000003")  # 同 race_id 別馬は別キー
    existing = _make_df([r1])
    new_df = _make_df([r1, r2, r3])
    merged, summary = merge_dataframes(existing, new_df)
    assert summary["new_unique_added"] == 2
    assert summary["duplicates_skipped"] == 1
    assert summary["merged_rows"] == 3


def test_merge_keeps_existing_on_collision():
    """既存優先: 同じ (race_id, horse_id) があれば既存を残し新規を捨てる。"""
    existing_row = _make_row("R20230101-東01", "00000001", jockey="既存騎手")
    new_row = _make_row("R20230101-東01", "00000001", jockey="新規騎手")
    existing = _make_df([existing_row])
    new_df = _make_df([new_row])
    merged, _ = merge_dataframes(existing, new_df)
    assert len(merged) == 1
    assert merged.iloc[0]["jockey"] == "既存騎手"


def test_merge_sorted_by_date_desc():
    """merged は race_date 降順でソートされる(既存運用慣習)。"""
    existing = _make_df([
        _make_row("R20230101-東01", "00000001", race_date="2023-01-01"),
        _make_row("R20240601-中01", "00000002", race_date="2024-06-01"),
    ])
    new_df = _make_df([
        _make_row("R20260510-新01", "00000003", race_date="2026-05-10"),
    ])
    merged, _ = merge_dataframes(existing, new_df)
    # 降順 = 一番新しいのが先頭
    assert merged.iloc[0]["race_date"] == "2026-05-10"
    assert merged.iloc[-1]["race_date"] == "2023-01-01"


def test_dedup_keys_constant():
    """DEDUP_KEYS は race_id + horse_id の 2 列。"""
    assert DEDUP_KEYS == ("race_id", "horse_id")


# ==================================================================
# バックアップ + 復元
# ==================================================================
def test_make_backup_and_restore(tmp_path):
    """バックアップ作成 → ファイル変更 → 復元 で元の内容に戻る。"""
    parquet = tmp_path / "races.parquet"
    df_v1 = _make_df([_make_row("R20230101-東01", "00000001")])
    df_v1.to_parquet(parquet, index=False)
    bak = _make_backup(parquet)
    assert bak.exists()
    assert ".bak." in bak.name

    # parquet を変えてしまう
    df_v2 = _make_df([_make_row("R20260510-新01", "00000002")])
    df_v2.to_parquet(parquet, index=False)
    assert pd.read_parquet(parquet)["horse_id"].iloc[0] == "00000002"

    # バックアップから復元
    restore_from_backup(bak, parquet)
    restored = pd.read_parquet(parquet)
    assert restored["horse_id"].iloc[0] == "00000001"


def test_restore_from_missing_backup_raises(tmp_path):
    """存在しないバックアップ → FileNotFoundError。"""
    import pytest
    parquet = tmp_path / "races.parquet"
    _make_df([_make_row("R20230101-東01", "00000001")]).to_parquet(parquet, index=False)
    with pytest.raises(FileNotFoundError):
        restore_from_backup(tmp_path / "nonexistent.bak", parquet)


# ==================================================================
# update_parquet エンドツーエンド(一時 parquet で完結)
# ==================================================================
def test_update_parquet_dry_run_does_not_modify(tmp_path):
    """--dry-run 相当: parquet が一切変更されない。"""
    parquet = tmp_path / "races.parquet"
    df_v1 = _make_df([_make_row("R20230101-東01", "00000001")])
    df_v1.to_parquet(parquet, index=False)
    original_mtime = parquet.stat().st_mtime

    # 取り込み用 CSV をその場で作る(最小限の SE 形式)
    csv_text = ",".join([
        "25", "07", "26", "1", "東京", "1", "01", "テスト", "7", "芝", "0", "1600",
        "良", "テスト馬", "牡", "3", "騎手", "55.0", "10", "01", "1", "1", "0",
        "0", "1", "1320", "100", "0", "0", "1", "1", "33.5", "500", "調教師",
        "栗", "0", "00000002", "00000", "00000", "0000000000", "馬主", "牧場",
        "父", "母", "母父", "鹿毛", "230301", "1.5", "", "", "50.0",
    ])
    csv_path = tmp_path / "sample.csv"
    csv_path.write_bytes(csv_text.encode("shift_jis"))

    summary = update_parquet(
        [csv_path], parquet_path=parquet, dry_run=True, make_backup=False,
    )
    assert summary["dry_run"] is True
    assert summary["new_unique_added"] == 1
    # parquet は無変更
    assert parquet.stat().st_mtime == original_mtime


def test_update_parquet_real_run_adds_row(tmp_path):
    """本番実行で parquet に行が追加される + バックアップが作られる。"""
    parquet = tmp_path / "races.parquet"
    df_v1 = _make_df([_make_row("R20230101-東01", "00000001")])
    df_v1.to_parquet(parquet, index=False)
    original_rows = len(pd.read_parquet(parquet))

    csv_text = ",".join([
        "25", "07", "26", "1", "東京", "1", "01", "テスト", "7", "芝", "0", "1600",
        "良", "テスト馬", "牡", "3", "騎手", "55.0", "10", "01", "1", "1", "0",
        "0", "1", "1320", "100", "0", "0", "1", "1", "33.5", "500", "調教師",
        "栗", "0", "00000002", "00000", "00000", "0000000000", "馬主", "牧場",
        "父", "母", "母父", "鹿毛", "230301", "1.5", "", "", "50.0",
    ])
    csv_path = tmp_path / "sample.csv"
    csv_path.write_bytes(csv_text.encode("shift_jis"))

    summary = update_parquet(
        [csv_path], parquet_path=parquet, dry_run=False, make_backup=True,
    )
    assert summary["dry_run"] is False
    assert summary["new_unique_added"] == 1
    after = pd.read_parquet(parquet)
    assert len(after) == original_rows + 1
    # バックアップが作成されている
    assert summary["backup_path"] is not None
    bak = Path(summary["backup_path"])
    assert bak.exists()
    # バックアップは取り込み前の状態
    bak_df = pd.read_parquet(bak)
    assert len(bak_df) == original_rows


def test_update_parquet_idempotent_reimport(tmp_path):
    """同じ CSV を 2 回取り込んでも 1 回目だけ追加され 2 回目は 0 件追加。"""
    parquet = tmp_path / "races.parquet"
    df_v1 = _make_df([_make_row("R20230101-東01", "00000001")])
    df_v1.to_parquet(parquet, index=False)

    csv_text = ",".join([
        "25", "07", "26", "1", "東京", "1", "01", "テスト", "7", "芝", "0", "1600",
        "良", "テスト馬", "牡", "3", "騎手", "55.0", "10", "01", "1", "1", "0",
        "0", "1", "1320", "100", "0", "0", "1", "1", "33.5", "500", "調教師",
        "栗", "0", "00000002", "00000", "00000", "0000000000", "馬主", "牧場",
        "父", "母", "母父", "鹿毛", "230301", "1.5", "", "", "50.0",
    ])
    csv_path = tmp_path / "sample.csv"
    csv_path.write_bytes(csv_text.encode("shift_jis"))

    s1 = update_parquet([csv_path], parquet_path=parquet, dry_run=False, make_backup=False)
    assert s1["new_unique_added"] == 1

    s2 = update_parquet([csv_path], parquet_path=parquet, dry_run=False, make_backup=False)
    assert s2["new_unique_added"] == 0
    assert s2["duplicates_skipped"] == 1


def test_update_parquet_missing_parquet_raises(tmp_path):
    """対象 parquet がない場合 FileNotFoundError。"""
    import pytest
    csv_path = tmp_path / "anything.csv"
    csv_path.write_bytes(b"dummy")
    with pytest.raises(FileNotFoundError):
        update_parquet([csv_path], parquet_path=tmp_path / "missing.parquet")
