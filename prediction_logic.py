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
    determine_main_pick_v2,
    extract_wide_candidates,
    extract_wide_candidates_v2,
    get_last_finishing_positions,
)
from utils.onmark_rules import collect_onmarks
from utils.race_history import determine_running_style, get_recent_runs_for_race
from utils.rating_engine import HorseRating, compute_horse_rating
from utils.rating_rules import HONMEI_RATING_THRESHOLD


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
# v1.0 / v1.1 戻り値型
# ==================================================================
@dataclass
class RacePrediction:
    """1レース分の予想結果。app.py のレンダラへの入力。

    Phase 6 で rating モードを追加。logic_mode="rating" のとき
    horse_ratings に HorseRating のリストが、logic_mode="onmark" のとき
    horses に HorseMarkData のリストが入る(両方同時に空でない場合あり)。
    """
    race_id: str
    race_meta: dict                    # {racecourse, race_number, race_name, distance, surface, going, post_time}
    horses: list[HorseMarkData]        # 全馬の○マーク + メタ(人気・脚質・枠番)
    judgment: JudgmentResult            # 本命判定結果
    wide_candidates: list[WideCandidate]
    betting: BettingPlan
    demerit_entries: list[DemeritEntry]
    # Phase 6: rating モード固有
    logic_mode: str = "onmark"                        # "onmark" or "rating"
    horse_ratings: list[HorseRating] = field(default_factory=list)


# ==================================================================
# Phase 6: ロジックモード切替フラグ
# ==================================================================
# "rating" にすると本ロジック v1.1(rating-based、≥100で◎)、
# "onmark" にすると本ロジック v1.0(○マーク、≥5で◎)。
# UI 側は predict_all_races_cached の戻り値内 race.logic_mode を見て分岐。
LOGIC_MODE: str = "rating"


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

    # 人気: race_card_df の popularity 列を優先(JRA データの確定単勝人気)。
    # 列が無い・全行欠損の場合のみ odds 昇順から再計算するフォールバック。
    if "popularity" in race_card_df.columns and race_card_df["popularity"].notna().any():
        pops = race_card_df["popularity"].reset_index(drop=True)
    else:
        pops = compute_popularities_from_odds(race_card_df["odds"]).reset_index(drop=True)

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
    """1レース分の v1.0 予想を実行する(○マーク方式)。"""
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
        logic_mode="onmark",
    )


def predict_race_v2(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    target_date: str | None = None,
) -> RacePrediction:
    """1レース分の v1.1 予想を実行する(rating 方式、≥100 で ◎)。

    内部ロジック:
    1. 各馬の HorseMarkData を組み立て(基本属性 + 直近5走履歴)
    2. その馬の今日の斤量を race_card から取得
    3. compute_horse_rating で C/D/E/F1/F2/F3 を評価して total_rating
    4. determine_main_pick_v2 (≥100 で◎、減点 B1/B2 適用、準◎ fallback)
    5. extract_wide_candidates_v2 (A2-A5 で最大3頭)
    6. 買い目生成は既存 v1 ヘルパを再利用(HorseMarkData 互換のため
       馬基本情報は HorseMarkData 経由で渡す)
    """
    if isinstance(historical, HistoricalData):
        hist_df = historical.races
    else:
        hist_df = historical

    meta = _build_race_meta(race_card_df)
    if target_date is None:
        target_date = meta.get("race_date", "")

    # 1) 馬基本情報(○マークなしで構築) — 後段の rating 計算で属性のみ使う
    horses_v1 = _build_horse_mark_data(race_card_df, hist_df, target_date)

    # 2) 直近5走を取得(rating engine への入力)
    horse_ids = [h.horse_id for h in horses_v1]
    history = get_recent_runs_for_race(
        tuple(horse_ids), target_date, hist_df, n=5,
    )

    # 3) 当日斤量(race_card_df の carry_weight 列から取得)
    carry_by_id: dict[str, float | None] = {}
    if "carry_weight" in race_card_df.columns:
        for _, row in race_card_df.iterrows():
            try:
                cw = float(row["carry_weight"]) if pd.notna(row["carry_weight"]) else None
            except (ValueError, TypeError):
                cw = None
            carry_by_id[str(row["horse_id"])] = cw

    # 4) 各馬の rating を計算
    horse_ratings: list[HorseRating] = []
    for h in horses_v1:
        runs = history.get(h.horse_id, [None] * 5)
        rating = compute_horse_rating(
            horse_id=h.horse_id,
            horse_name=h.horse_name,
            horse_number=h.horse_number,
            frame_number=h.frame_number,
            popularity=h.popularity,
            running_style=h.running_style,
            last_finishing_position=h.last_finishing_position,
            today_carry_weight=carry_by_id.get(h.horse_id),
            past_runs=runs,
            race_meta=meta,
        )
        horse_ratings.append(rating)

    # 5) 本命判定 + 減点
    judgment = determine_main_pick_v2(horse_ratings, meta, threshold=HONMEI_RATING_THRESHOLD)

    # 6) ワイド候補(A2-A5、最大3頭)
    wides = extract_wide_candidates_v2(horse_ratings, meta)

    # 7) Step 5 R2 偶奇フィルタは v1 ヘルパを使う(HorseMarkData 経由)
    wides = filter_by_frame_parity(wides, horses_v1)

    # 8) 買い目生成(v1 ヘルパが HorseMarkData を期待するため horses_v1 を渡す)
    betting = generate_betting_recommendations(
        judgment.main_pick, judgment.sub_pick, wides, horses_v1
    )

    return RacePrediction(
        race_id=meta.get("race_id", ""),
        race_meta=meta,
        horses=horses_v1,
        judgment=judgment,
        wide_candidates=wides,
        betting=betting,
        demerit_entries=judgment.demerit_entries,
        logic_mode="rating",
        horse_ratings=horse_ratings,
    )


def predict_race_dc(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    target_date: str | None = None,
) -> RacePrediction:
    """DC 形式 CSV 専用の簡易予想。

    DC 形式は 馬名・騎手・上3F・通過順位・馬場・過去走 race_date を持たないため
    本来の C/D/E/F1/F2/F3 ルール群はすべて発火不可能。代替として:

    - **TARGET 指数(col[5])** を直接 rating 値として採用
    - 過去 7 走は race_card_df.attrs["dc_past_runs"] から取得(DC ファイル
      自体に同梱されている)
    - 減点 B1/B2 は人気・脚質情報が無いため適用しない
    - ワイド候補は TARGET 指数 上位馬から採る(同 priority weight)

    本関数の戻り値は logic_mode="dc" の RacePrediction。app.py のレンダラは
    既存の RA+SE 用パスを再利用しつつ、DC 固有のセクション(過去走マトリクス
    が DC 形式の付属データを使う等)を分岐表示する。
    """
    meta = _build_race_meta(race_card_df)
    if target_date is None:
        target_date = meta.get("race_date", "")

    past_runs_by_horse = race_card_df.attrs.get("dc_past_runs", {})

    # 1) HorseRating 相当のリストを TARGET 指数から組み立て
    horse_ratings: list[HorseRating] = []
    for _, row in race_card_df.reset_index(drop=True).iterrows():
        hid = str(row["horse_id"])
        try:
            hn = int(row["horse_number"]) if pd.notna(row["horse_number"]) else 0
        except (ValueError, TypeError):
            hn = 0
        try:
            ti = float(row["target_index"]) if pd.notna(row["target_index"]) else 0.0
        except (ValueError, TypeError):
            ti = 0.0
        rating = HorseRating(
            horse_id=hid,
            horse_name=str(row["horse_name"]),
            horse_number=hn,
            frame_number=0,             # DC では枠番不明 → 0
            popularity=0,                # DC では人気不明 → 0
            running_style="不明(先行扱い)",  # 通過順位不明
            total_rating=int(round(ti)),
            matched=[],                  # ルール集計しない(rating = TARGET 指数のみ)
            last_finishing_position=None,
            today_carry_weight=None,
            rule24_active=False,
        )
        horse_ratings.append(rating)

    # 2) 本命判定: rating ≥ HONMEI_RATING_THRESHOLD(=100)を◎候補に
    judgment = determine_main_pick_v2(
        horse_ratings, meta, threshold=HONMEI_RATING_THRESHOLD,
    )

    # 3) ワイド候補: 軸馬を除いた rating 上位 2-3 頭を A2 相当で出す
    axis_id = judgment.main_pick or judgment.sub_pick
    sorted_ratings = sorted(horse_ratings, key=lambda r: -r.total_rating)
    wides: list[WideCandidate] = []
    for r in sorted_ratings:
        if r.horse_id == axis_id:
            continue
        if r.total_rating < 80:  # DC では指数 80 未満は候補から外す
            continue
        wides.append(WideCandidate(
            horse_id=r.horse_id,
            horse_name=r.horse_name,
            horse_number=r.horse_number,
            popularity=0,
            matched_rules=["DC"],
            reasons=[f"DC: TARGET 指数 {r.total_rating}"],
            priority=r.total_rating,
        ))
        if len(wides) >= 3:
            break

    # 4) 簡易な HorseMarkData リスト(UI 互換用に最小限の値を入れる)
    horses_v1: list[HorseMarkData] = []
    for r in horse_ratings:
        horses_v1.append(HorseMarkData(
            horse_id=r.horse_id,
            horse_name=r.horse_name,
            horse_number=r.horse_number,
            frame_number=r.frame_number,
            popularity=r.popularity,
            running_style=r.running_style,
            marks_count=0,
            matched_rules=[],
            last_finishing_position=None,
        ))

    # 5) 買い目生成(既存ヘルパで OK)
    betting = generate_betting_recommendations(
        judgment.main_pick, judgment.sub_pick, wides, horses_v1
    )

    pred = RacePrediction(
        race_id=meta.get("race_id", ""),
        race_meta=meta,
        horses=horses_v1,
        judgment=judgment,
        wide_candidates=wides,
        betting=betting,
        demerit_entries=judgment.demerit_entries,
        logic_mode="dc",
        horse_ratings=horse_ratings,
    )
    # 過去走を pred の race_meta に格納(レンダラが参照する)
    pred.race_meta["dc_past_runs"] = {
        hid: runs for hid, runs in past_runs_by_horse.items()
        if any(r.race_id == race_card_df.iloc[0]["race_id"]
               for r in [pred] if hid in {h.horse_id for h in horses_v1})
    }
    # シンプルに同レースの全馬の過去走を格納
    pred.race_meta["dc_past_runs"] = {
        h.horse_id: past_runs_by_horse.get(h.horse_id, [None] * 5)
        for h in horses_v1
    }
    return pred


def predict_race(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    target_date: str | None = None,
    *,
    mode: str | None = None,
) -> RacePrediction:
    """データ形式と LOGIC_MODE に従って予想関数を dispatch。

    DC 形式(race_card_df.attrs["data_format"] == "dc")が最優先で、
    TARGET 指数ベースの簡易予想 (predict_race_dc) を起動する。
    それ以外は LOGIC_MODE / mode 引数で v1 / v2 を切り替え。
    """
    if race_card_df.attrs.get("data_format") == "dc":
        return predict_race_dc(race_card_df, historical, target_date)

    chosen = mode or LOGIC_MODE
    if chosen == "rating":
        return predict_race_v2(race_card_df, historical, target_date)
    return predict_race_v1(race_card_df, historical, target_date)


def predict_all_races_v1(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    *,
    mode: str | None = None,
) -> dict[str, RacePrediction]:
    """出馬表全体を race_id 単位で予想する(LOGIC_MODE / mode 引数で切替)。

    pandas の groupby は df.attrs を子グループに伝播しないため、ここで明示的に
    attrs(data_format / dc_past_runs)をコピーして predict_race に渡す。
    """
    parent_attrs = dict(race_card_df.attrs or {})
    results: dict[str, RacePrediction] = {}
    for race_id, group in race_card_df.groupby("race_id", sort=False):
        # groupby 後の group には attrs が乗っていないので親の attrs を引き継ぐ
        group.attrs = parent_attrs
        target_date = str(group["race_date"].iloc[0])
        results[str(race_id)] = predict_race(group, historical, target_date, mode=mode)
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
