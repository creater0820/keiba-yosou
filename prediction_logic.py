"""
予想ロジック本体(本ロジック v1.0)。

CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」を統合的に走らせる。

Phase 2: utils/onmark_rules.py(○マーク収集)
Phase 3: utils/judgment_engine.py(本命判定 / 減点 / ワイド候補)
Phase 4: utils/betting_strategy.py(ダート不良補正 / 偶奇絞り / 買い目生成)

外部 API:
- predict_race_v1(race_card_df, historical, target_date) → RacePrediction
- predict_all_races_v1(race_card_df, historical) → dict[race_id → RacePrediction]
- HorsePrediction(後方互換: app.py の旧シグネチャを保持するため最小実装)
- predict_all_races_cached: app.py の Streamlit @st.cache_data エントリポイント

設計方針:
- 既存の MVP スコアリングは廃止(本ロジック v1.0 へ完全置換)。
- 全関数 race_card_df / HistoricalData の DataFrame アクセスのみで完結。
- 計算結果は dataclass 化 → app.py のレンダラがそのまま読める。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from data_loader import HistoricalData
from utils.betting_strategy import (
    BettingPlan,
    apply_dirt_heavy_correction,
    filter_by_frame_parity,
    generate_betting_recommendations,
)
from utils.frame_number import horse_number_to_frame
from utils.judgment_engine import (
    DemeritEntry,
    HorseMarkData,
    JudgmentResult,
    WideCandidate,
    compute_popularities_from_odds,
    determine_main_pick,
    extract_wide_candidates,
    get_last_finishing_positions,
)
from utils.onmark_rules import collect_onmarks
from utils.race_history import determine_running_style, get_recent_runs_for_race


# ==================================================================
# 旧 API の最小互換レイヤ
# ==================================================================
# app.py は HorsePrediction / predict_all_races_cached を import している。
# v1.0 では使わない設計だが、import エラーを回避するため最小定義を残す。
@dataclass
class HorsePrediction:
    """後方互換のための薄い shim(v1.0 では未使用)。"""
    horse_id: str = ""
    horse_name: str = ""
    jockey: str = ""
    score: float = 0.0
    mark: str = ""
    reasons: list[str] = field(default_factory=list)


# ==================================================================
# v1.0 戻り値型
# ==================================================================
@dataclass
class RacePrediction:
    """v1.0 1レース分の予想結果。app.py のレンダラへの入力。"""
    race_id: str
    race_meta: dict                    # {racecourse, race_number, race_name, distance, surface, going, post_time}
    horses: list[HorseMarkData]        # 全馬の○マーク + メタ(人気・脚質・枠番)
    judgment: JudgmentResult            # 本命判定結果
    wide_candidates: list[WideCandidate]
    betting: BettingPlan
    demerit_entries: list[DemeritEntry]


# ==================================================================
# データ準備ヘルパ
# ==================================================================

def _build_horse_mark_data(
    race_card_df: pd.DataFrame,
    historical_df: pd.DataFrame,
    target_date: str,
) -> list[HorseMarkData]:
    """1レース分の race_card + historical から HorseMarkData リストを組み立てる。"""
    field_size = len(race_card_df)
    horse_ids = race_card_df["horse_id"].astype(str).tolist()

    # 直近5走を一括キャッシュ取得
    history = get_recent_runs_for_race(
        tuple(horse_ids), target_date, historical_df, n=5,
    )
    # 前走着順 (rule_4 用)
    last_pos = get_last_finishing_positions(horse_ids, target_date, historical_df)
    # 人気
    pops = compute_popularities_from_odds(race_card_df["odds"])

    horses: list[HorseMarkData] = []
    for idx, row in race_card_df.reset_index(drop=True).iterrows():
        hid = str(row["horse_id"])
        try:
            hn = int(row["horse_number"]) if pd.notna(row["horse_number"]) else 0
        except (ValueError, TypeError):
            hn = 0
        try:
            frame = horse_number_to_frame(hn, field_size) if hn else 0
        except ValueError:
            frame = 0

        recent = history.get(hid, [])
        style = determine_running_style(recent)
        marks_count, matched = collect_onmarks(recent)

        try:
            popularity = int(pops.iloc[idx]) if not pd.isna(pops.iloc[idx]) else 0
        except (IndexError, ValueError, TypeError):
            popularity = 0

        horses.append(HorseMarkData(
            horse_id=hid,
            horse_name=str(row["horse_name"]).strip(),
            horse_number=hn,
            frame_number=frame,
            popularity=popularity,
            running_style=style,
            marks_count=marks_count,
            matched_rules=matched,
            last_finishing_position=last_pos.get(hid),
        ))
    return horses


def _build_race_meta(race_card_df: pd.DataFrame) -> dict:
    """race_card_df の先頭1行からレースメタ情報を抜く。"""
    if race_card_df.empty:
        return {}
    head = race_card_df.iloc[0]
    return {
        "race_id": str(head.get("race_id", "")),
        "race_date": str(head.get("race_date", "")),
        "racecourse": str(head.get("racecourse", "")).strip(),
        "race_number": int(head["race_number"]) if pd.notna(head.get("race_number")) else 0,
        "race_name": str(head.get("race_name", "")).strip(),
        "distance": int(head["distance"]) if pd.notna(head.get("distance")) else 0,
        "surface": str(head.get("surface", "")).strip(),
        "going": str(head.get("going", "")).strip(),
        "post_time": str(head.get("post_time", "")).strip(),
    }


# ==================================================================
# v1.0 メインエントリ
# ==================================================================

def predict_race_v1(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    target_date: str | None = None,
) -> RacePrediction:
    """1レース分の v1.0 予想を実行する。"""
    if isinstance(historical, HistoricalData):
        hist_df = historical.races
    else:
        hist_df = historical

    meta = _build_race_meta(race_card_df)
    if target_date is None:
        target_date = meta.get("race_date", "")

    # Phase 2: ○マーク収集
    horses = _build_horse_mark_data(race_card_df, hist_df, target_date)

    # Phase 4 補正(R23): ダート不良で逃げに ○+1
    horses = apply_dirt_heavy_correction(horses, meta)

    # Phase 3: 本命判定 + 減点
    judgment = determine_main_pick(horses, meta)

    # Phase 3: ワイド候補抽出
    wides = extract_wide_candidates(horses, meta)

    # Phase 4 (R2): 偶奇フィルタ
    wides = filter_by_frame_parity(wides, horses)

    # Phase 4: 買い目生成
    betting = generate_betting_recommendations(
        judgment.main_pick, judgment.sub_pick, wides, horses
    )

    return RacePrediction(
        race_id=meta.get("race_id", ""),
        race_meta=meta,
        horses=horses,
        judgment=judgment,
        wide_candidates=wides,
        betting=betting,
        demerit_entries=judgment.demerit_entries,
    )


def predict_all_races_v1(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
) -> dict[str, RacePrediction]:
    """出馬表全体を race_id 単位で v1.0 予想する。"""
    results: dict[str, RacePrediction] = {}
    for race_id, group in race_card_df.groupby("race_id", sort=False):
        target_date = str(group["race_date"].iloc[0])
        results[str(race_id)] = predict_race_v1(group, historical, target_date)
    return results


# ==================================================================
# Streamlit @st.cache_data エントリポイント
# ==================================================================
# 旧名: predict_all_races_cached(race_card_hash, _race_card_df, _historical)
# v1.0 でも同名で残す(app.py 側の呼び出し変更なし)。

@st.cache_data(show_spinner="予想計算中…")
def predict_all_races_cached(
    race_card_hash: str,
    _race_card_df: pd.DataFrame,
    _historical: HistoricalData,
) -> dict[str, RacePrediction]:
    """
    race_card_hash でキャッシュされる v1.0 予想エントリポイント。
    _race_card_df と _historical は ハッシュ対象外(_ 接頭辞)。
    """
    return predict_all_races_v1(_race_card_df, _historical)
