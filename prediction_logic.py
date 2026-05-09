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

    # 直近10走を一括キャッシュ取得(v1.4: 5→10 に拡張)
    # 脚質判定は内部で head5 に絞るので、10 走入力でも安全。
    history = get_recent_runs_for_race(
        tuple(horse_ids), target_date, historical_df, n=10,
    )
    # 前走着順 (rule_4 用)
    last_pos = get_last_finishing_positions(horse_ids, target_date, historical_df)

    # 人気: race_card_df の popularity 列を優先(JRA データの確定単勝人気)。
    # 列が無い・全行欠損の場合のみ odds 昇順から再計算するフォールバック。
    if "popularity" in race_card_df.columns and race_card_df["popularity"].notna().any():
        pops = race_card_df["popularity"].reset_index(drop=True)
    else:
        pops = compute_popularities_from_odds(race_card_df["odds"]).reset_index(drop=True)

    # **perf**: 旧実装は race_card_df.iterrows() で 13-18 行を Python ループ
    # していたが、レース 34 回累積で 数秒の hot path だった。to_dict("records")
    # で plain dict のリストに変換すると 50-100 倍速い。
    rc_records = race_card_df.reset_index(drop=True)[
        ["horse_id", "horse_number", "horse_name"]
    ].to_dict("records")
    pops_arr = pops.to_numpy() if hasattr(pops, "to_numpy") else list(pops)

    horses: list[HorseMarkData] = []
    for idx, row in enumerate(rc_records):
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
            pop_val = pops_arr[idx]
            popularity = int(pop_val) if not pd.isna(pop_val) else 0
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
    *,
    training_match: dict[str, dict] | None = None,
) -> RacePrediction:
    """1レース分の v1.1+ 予想を実行する(rating 方式、≥100 で ◎)。

    内部ロジック:
    1. 各馬の HorseMarkData を組み立て(基本属性 + 直近10走履歴 ※脚質判定は head5)
    2. その馬の今日の斤量を race_card から取得
    3. compute_horse_rating で C/D/E/F1/F2/F3/F4/F5 を評価して total_rating
    4. determine_main_pick_v2 (≥100 で◎、減点 B1/B2 適用、準◎ fallback)
    5. extract_wide_candidates_v2 (A2-A5 で最大3頭)
    6. 買い目生成は既存 v1 ヘルパを再利用(HorseMarkData 互換のため
       馬基本情報は HorseMarkData 経由で渡す)

    引数(v1.5 追加):
        training_match: utils/training_data.match_training_to_horses の戻り値。
                        {horse_id: {"lap1": float, "lap2": float, ...}}
                        None なら F4/F5 永続無効(missed_rule_ids 入り)。
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

    # 2) 直近10走を取得(rating engine への入力、v1.4: 5→10 に拡張)
    horse_ids = [h.horse_id for h in horses_v1]
    history = get_recent_runs_for_race(
        tuple(horse_ids), target_date, hist_df, n=10,
    )

    # 3) 当日斤量(race_card_df の carry_weight 列から取得)
    # **perf**: iterrows 回避、to_dict("records") で plain dict にして高速化
    carry_by_id: dict[str, float | None] = {}
    if "carry_weight" in race_card_df.columns:
        for rec in race_card_df[["horse_id", "carry_weight"]].to_dict("records"):
            try:
                cw_val = rec["carry_weight"]
                cw = float(cw_val) if pd.notna(cw_val) else None
            except (ValueError, TypeError):
                cw = None
            carry_by_id[str(rec["horse_id"])] = cw

    # 4) 各馬の rating を計算(training_match があれば F4/F5 評価も走る)
    horse_ratings: list[HorseRating] = []
    for h in horses_v1:
        runs = history.get(h.horse_id, [None] * 10)
        td = training_match.get(h.horse_id) if training_match else None
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
            training_data=td,
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
    *,
    training_match: dict[str, dict] | None = None,
) -> RacePrediction:
    """DC 形式 CSV 専用の予想(v1.5 純粋ロジックモード + 坂路 F4/F5)。

    動作モード(レース内の馬ごとに自動切替):
    - **フルモード**: 過去走パターンマッチで historical horse_id が特定でき、
      historical の上3F・通過順位・馬場 を取得できた馬。
      → C/D/E/F1/F2/F3 ルールで rating を計算(本ロジック v1.1 完全準拠)
    - **簡易モード**: マッチ失敗馬。
      → TARGET 指数(col[5])を rating として採用

    人気は TARGET 指数の同レース内ランクで代用(1=最高指数)。
    これで A1/A2/A4/A5/B1 ルールも条件次第で発火可能になる。

    判定 / ワイド候補抽出 / 減点 / 買い目生成 は既存 v1.1 のヘルパを
    そのまま再利用する(RA+SE と同じ rating 値ベースのロジック)。
    """
    if isinstance(historical, HistoricalData):
        hist_df = historical.races
    else:
        hist_df = historical

    meta = _build_race_meta(race_card_df)
    if target_date is None:
        target_date = meta.get("race_date", "")

    past_runs_by_horse = race_card_df.attrs.get("dc_past_runs", {})
    today_going = str(race_card_df.attrs.get("dc_going") or
                       race_card_df["going"].iloc[0] if not race_card_df.empty else "" or "良")
    if today_going not in ("良", "稍重", "稍", "重", "不良"):
        today_going = "良"
    # race_meta の going を上書き(rating engine が読む)
    meta["going"] = today_going

    field_size = len(race_card_df)
    horses_df = race_card_df.reset_index(drop=True)

    # ----- 人気を TARGET 指数 のレース内ランクで再計算 -----
    target_indices = pd.to_numeric(horses_df.get("target_index"), errors="coerce")
    pop_rank = target_indices.rank(method="min", ascending=False, na_option="bottom")
    pop_rank = pop_rank.fillna(0).astype(int)

    # ----- 各馬の HorseRating + HorseMarkData を組み立て -----
    # **perf**: iterrows を撤去、to_dict("records") + numpy 化で高速化
    has_matched_col = "matched_historical_horse_id" in horses_df.columns
    cols_for_rec = [
        c for c in [
            "horse_id", "horse_number", "horse_name", "target_index",
            "matched_historical_horse_id",
        ] if c in horses_df.columns
    ]
    horses_records = horses_df[cols_for_rec].to_dict("records")
    pop_rank_arr = pop_rank.to_numpy()

    horse_ratings: list[HorseRating] = []
    horses_v1: list[HorseMarkData] = []
    full_mode_count = 0
    for idx, row in enumerate(horses_records):
        hid = str(row["horse_id"])
        try:
            hn = int(row["horse_number"]) if pd.notna(row["horse_number"]) else 0
        except (ValueError, TypeError):
            hn = 0
        try:
            ti_val = row.get("target_index")
            ti = float(ti_val) if pd.notna(ti_val) else 0.0
        except (ValueError, TypeError):
            ti = 0.0
        try:
            popularity = int(pop_rank_arr[idx])
        except (IndexError, ValueError, TypeError):
            popularity = 0
        try:
            frame = horse_number_to_frame(hn, field_size) if hn else 0
        except ValueError:
            frame = 0

        runs = past_runs_by_horse.get(hid, [None] * 10)
        # フルモード判定: matched_historical_horse_id 列があり、かつ
        # past_runs に last_3f / corner_1 がある(historical 由来)なら full
        matched_hist_id = row.get("matched_historical_horse_id") if has_matched_col else None
        is_full = matched_hist_id is not None and any(
            (r and r.get("last_3f") is not None) for r in runs
        )

        if is_full:
            # フルモード: 脚質を historical 通過順位から判定 + rating engine を起動
            # **v1.3 純粋ロジック**: rating = ルール加算合計のみ。
            # TARGET 指数(ZI)は HorseRating.target_index に参考値として保持
            # するが total_rating には含めない。お父様独自ロジック(C/D/E/F)の
            # 真の発火率で順位付けする方針。
            full_mode_count += 1
            running_style = determine_running_style(runs)
            last_pos = runs[0].get("finishing_position") if runs and runs[0] else None
            td = training_match.get(hid) if training_match else None
            rule_obj = compute_horse_rating(
                horse_id=hid,
                horse_name=str(row["horse_name"]),
                horse_number=hn,
                frame_number=frame,
                popularity=popularity,
                running_style=running_style,
                last_finishing_position=last_pos,
                today_carry_weight=None,  # DC は当日斤量持たず F3 は永続無効
                past_runs=runs,
                race_meta=meta,
                training_data=td,
            )
            rating_obj = HorseRating(
                horse_id=rule_obj.horse_id,
                horse_name=rule_obj.horse_name,
                horse_number=rule_obj.horse_number,
                frame_number=rule_obj.frame_number,
                popularity=rule_obj.popularity,
                running_style=rule_obj.running_style,
                total_rating=rule_obj.total_rating,  # TARGET 指数を加算しない
                matched=rule_obj.matched,
                last_finishing_position=rule_obj.last_finishing_position,
                today_carry_weight=rule_obj.today_carry_weight,
                rule24_active=rule_obj.rule24_active,
                evaluated_rule_ids=rule_obj.evaluated_rule_ids,
                missed_rule_ids=rule_obj.missed_rule_ids,
                target_index=int(round(ti)),  # 参考値として保持
            )
        else:
            # 簡易モード(マッチ失敗): rating = 0(評価不能を明示)。
            # TARGET 指数は target_index に「参考値」として保持。
            running_style = "不明(先行扱い)"
            rating_obj = HorseRating(
                horse_id=hid,
                horse_name=str(row["horse_name"]),
                horse_number=hn,
                frame_number=frame,
                popularity=popularity,
                running_style=running_style,
                total_rating=0,  # ルール評価不能 → 0
                matched=[],
                last_finishing_position=None,
                today_carry_weight=None,
                rule24_active=False,
                target_index=int(round(ti)),  # 参考値として保持
            )

        horse_ratings.append(rating_obj)
        horses_v1.append(HorseMarkData(
            horse_id=hid,
            horse_name=str(row["horse_name"]),
            horse_number=hn,
            frame_number=frame,
            popularity=popularity,
            running_style=running_style,
            marks_count=0,
            matched_rules=[],
            last_finishing_position=rating_obj.last_finishing_position,
        ))

    # ----- 本命判定 + 減点(B1/B2)+ ワイド候補 -----
    # **v1.3 純粋ロジック**: rating = ルール加算合計のみ(TARGET 指数除外)。
    # 閾値は RA+SE と同じ HONMEI_RATING_THRESHOLD = 100 に統一。
    # 「お父様のルールが 100 点分発火した馬」だけが ◎本命候補になる。
    judgment = determine_main_pick_v2(
        horse_ratings, meta, threshold=HONMEI_RATING_THRESHOLD,
    )
    wides = extract_wide_candidates_v2(horse_ratings, meta)
    wides = filter_by_frame_parity(wides, horses_v1)

    # ----- 買い目生成 -----
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
    pred.race_meta["dc_past_runs"] = {
        h.horse_id: past_runs_by_horse.get(h.horse_id, [None] * 10)
        for h in horses_v1
    }
    pred.race_meta["dc_full_mode_count"] = full_mode_count
    pred.race_meta["dc_total_count"] = len(horses_v1)
    return pred


def predict_race(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    target_date: str | None = None,
    *,
    mode: str | None = None,
    training_match: dict[str, dict] | None = None,
) -> RacePrediction:
    """データ形式と LOGIC_MODE に従って予想関数を dispatch。

    DC 形式(race_card_df.attrs["data_format"] == "dc")が最優先で、
    TARGET 指数ベースの簡易予想 (predict_race_dc) を起動する。
    それ以外は LOGIC_MODE / mode 引数で v1 / v2 を切り替え。

    training_match (v1.5): 坂路調教マッチ結果。F4/F5 評価に使う。None 可。
    """
    if race_card_df.attrs.get("data_format") == "dc":
        return predict_race_dc(
            race_card_df, historical, target_date,
            training_match=training_match,
        )

    chosen = mode or LOGIC_MODE
    if chosen == "rating":
        return predict_race_v2(
            race_card_df, historical, target_date,
            training_match=training_match,
        )
    # onmark v1.0 は F4/F5 を持たないので training_match は無視
    return predict_race_v1(race_card_df, historical, target_date)


def predict_all_races_v1(
    race_card_df: pd.DataFrame,
    historical: HistoricalData | pd.DataFrame,
    *,
    mode: str | None = None,
    training_match: dict[str, dict] | None = None,
) -> dict[str, RacePrediction]:
    """出馬表全体を race_id 単位で予想する(LOGIC_MODE / mode 引数で切替)。

    pandas の groupby は df.attrs を子グループに伝播しないため、ここで明示的に
    attrs(data_format / dc_past_runs)をコピーして predict_race に渡す。

    training_match (v1.5): 全馬の坂路調教マッチ結果(horse_id → dict)。
    レース単位で predict_race に渡される(各レースは自馬の training_data
    だけ参照する)。
    """
    parent_attrs = dict(race_card_df.attrs or {})
    results: dict[str, RacePrediction] = {}
    for race_id, group in race_card_df.groupby("race_id", sort=False):
        # groupby 後の group には attrs が乗っていないので親の attrs を引き継ぐ
        group.attrs = parent_attrs
        target_date = str(group["race_date"].iloc[0])
        results[str(race_id)] = predict_race(
            group, historical, target_date,
            mode=mode, training_match=training_match,
        )
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
    training_hash: str = "",
    _training_match: dict[str, dict] | None = None,
) -> dict[str, RacePrediction]:
    """
    race_card_hash + training_hash でキャッシュされる予想エントリポイント。
    _race_card_df / _historical / _training_match は _ 接頭辞でハッシュ対象外。

    v1.5: training_hash と _training_match を追加。
    training_hash が空文字なら坂路 CSV 未アップロード扱い(F4/F5 永続無効)。
    別の training CSV をアップロードすると hash が変わり、キャッシュミスして
    再計算される。
    """
    return predict_all_races_v1(
        _race_card_df, _historical, training_match=_training_match,
    )
