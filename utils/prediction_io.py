"""
予想結果(JSON)の入出力ユーティリティ。

設計方針:
- Streamlit Cloud には永続ストレージが無いため、予想は JSON 1 ファイル / 1 レースで
  ブラウザにダウンロード → ユーザがリポジトリの predictions/ にコミット、という運用。
- このモジュール自体は Streamlit に依存せず、純粋な pandas / 標準ライブラリで完結。
- 突合(hit_matching.py)とダッシュボード(pages/02_的中履歴.py)から参照される。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# 予想 JSON の保存先(リポジトリにコミット対象)
PREDICTIONS_DIR = Path("predictions")


def get_prediction_path(race_date: str, racecourse: str, race_number: int) -> Path:
    """
    予想 JSON のファイルパスを決定する。
    形式: predictions/YYYY-MM-DD_場名_RR.json

    Args:
        race_date: ISO 形式の日付文字列(例: "2026-05-03")
        racecourse: 競馬場の漢字名(例: "東京")
        race_number: レース番号(1〜12)
    """
    return PREDICTIONS_DIR / f"{race_date}_{racecourse}_{race_number:02d}R.json"


def build_prediction_dict(
    race_info: dict[str, Any],
    recommendations: list[Any],
    logic_version: str,
) -> dict[str, Any]:
    """
    1レース分の予想を JSON シリアライズ可能な dict に組み立てる。

    Args:
        race_info: race_id, race_date, racecourse, race_number, race_name,
                   distance, surface 等を含む dict(race_card_df の1行から作る想定)
        recommendations: HorsePrediction オブジェクト or 同等構造の dict のリスト
                         (印・スコア・理由等を含む)
        logic_version: 予想ロジックのバージョン文字列(後で精度比較に使う)

    Returns:
        JSON にダンプ可能な dict
    """
    # HorsePrediction (dataclass) と dict の両方を受け付ける
    serialized_recs = []
    for rank, rec in enumerate(recommendations, start=1):
        if hasattr(rec, "__dataclass_fields__"):
            d = asdict(rec)
        elif isinstance(rec, dict):
            d = dict(rec)
        else:
            raise TypeError(f"recommendations の要素は dataclass か dict である必要があります: {type(rec)}")

        serialized_recs.append({
            "mark": d.get("mark", ""),
            "rank": rank,
            "horse_id": str(d.get("horse_id", "")),
            "horse_name": str(d.get("horse_name", "")),
            "score": float(d.get("score", 0.0)),
            "reason": " | ".join(d.get("reasons", [])) if isinstance(d.get("reasons"), list) else str(d.get("reason", "")),
        })

    return {
        "race_id": str(race_info["race_id"]),
        "race_date": str(race_info["race_date"]),
        "racecourse": str(race_info["racecourse"]),
        "race_number": int(race_info["race_number"]) if pd.notna(race_info.get("race_number")) else None,
        "race_name": str(race_info.get("race_name", "")),
        "distance": int(race_info["distance"]) if pd.notna(race_info.get("distance")) else None,
        "surface": str(race_info.get("surface", "")),
        "predicted_at": datetime.now().isoformat(timespec="seconds"),
        "logic_version": logic_version,
        "recommendations": serialized_recs,
    }


def serialize_prediction(prediction_dict: dict[str, Any]) -> str:
    """予想 dict を JSON 文字列に変換(Unicode はそのまま、インデント2)。"""
    return json.dumps(prediction_dict, ensure_ascii=False, indent=2)


def save_prediction_to_disk(prediction_dict: dict[str, Any], dir_: Path | None = None) -> Path:
    """
    予想 dict を disk に書き出す(seed スクリプト等で使用)。
    Streamlit からは呼ばない(Streamlit Cloud は永続ストレージ無しのため)。
    """
    if dir_ is None:
        dir_ = PREDICTIONS_DIR
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"{prediction_dict['race_date']}_{prediction_dict['racecourse']}_{int(prediction_dict['race_number']):02d}R.json"
    path.write_text(serialize_prediction(prediction_dict), encoding="utf-8")
    return path


def load_predictions(dir_: Path | None = None) -> pd.DataFrame:
    """
    predictions/*.json をすべて読み込んで、推奨馬1頭を1行とするフラットな DataFrame で返す。

    返り列:
        race_id, race_date, racecourse, race_number, race_name, distance, surface,
        predicted_at, logic_version, mark, rank, horse_id, horse_name, score, reason
    """
    if dir_ is None:
        dir_ = PREDICTIONS_DIR
    if not dir_.exists():
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for path in sorted(dir_.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # 壊れた JSON は警告だけ出してスキップ(全体は止めない)
            print(f"⚠ {path.name}: JSON parse error: {e}")
            continue

        race_meta = {
            "race_id": data.get("race_id"),
            "race_date": data.get("race_date"),
            "racecourse": data.get("racecourse"),
            "race_number": data.get("race_number"),
            "race_name": data.get("race_name"),
            "distance": data.get("distance"),
            "surface": data.get("surface"),
            "predicted_at": data.get("predicted_at"),
            "logic_version": data.get("logic_version"),
        }
        for rec in data.get("recommendations", []):
            rows.append({
                **race_meta,
                "mark": rec.get("mark", ""),
                "rank": rec.get("rank"),
                "horse_id": str(rec.get("horse_id", "")),
                "horse_name": rec.get("horse_name", ""),
                "score": rec.get("score"),
                "reason": rec.get("reason", ""),
            })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    return df
