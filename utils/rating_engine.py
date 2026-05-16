"""
本ロジック v1.4 / Rating-based 判定の **計算エンジン**。

各馬の **直近10走** + 当日メタを入力に、CLAUDE.md「ロジック v1.4」の C/D/E/F
ルールを順次評価して合計 rating を返す。判定エンジン v2 は本モジュールの
出力 (HorseRating) を消費して ◎本命を決める。

v1.4 で評価範囲を 5 走 → 10 走に拡張。脚質判定(determine_running_style)は
別経路で head5(直近の脚質傾向重視)、戦歴マトリクス UI も 5 走表示のまま。

主要関数:
- compute_horse_rating(past_runs, today_horse_ctx, race_meta, policy)
    → HorseRating(total_rating, matched_rules, ...)

集計ポリシー (RatingPolicy.STRICT、デフォルト):
1. C/D/E の上3F 系は同一過去走で複数該当しうるが、過去走 1 行ごとに
   「最高 rate のもの」のみ採用(over-counting 防止)。
2. 同一 rule_id が複数走で発火 → 1 回までしか total に加算しない
   (例: C13 が 8走前 と 3走前 で発火 → +50 一度のみ)。
3. F2 救済(休養明け前走凡走)が発動した場合、評価対象を 2,3走前 に
   絞って C/D/E を再評価し、1 本でも該当すれば F2 自体も +15。
4. F1 (ダ不良 + 逃げ) / F3 (1600m+ + 斤量-3kg) は当日コンテキストで判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from utils.onmark_rules import (
    detect_rule_24_situation,
    evaluate_rule,
)
from utils.rating_rules import (
    DEFAULT_POLICY,
    RATING_RULES_C,
    RATING_RULES_D,
    RATING_RULES_E,
    RATING_RULES_F,
    RatingPolicy,
    RatingRule,
    find_rating_rule,
)


# =====================================================================
# データキャリア
# =====================================================================

@dataclass(frozen=True)
class RatingHit:
    """1 ルール × 1 過去走(または当日コンテキスト)での発火記録。"""
    rule_id: str
    rate: int
    reason: str
    run_idx: int    # 0=前走, 1=2走前, ..., -1=当日コンテキスト発火(F1, F3 等)


@dataclass
class HorseRating:
    """馬 1 頭の rating 計算結果。判定エンジン v2 への入力。"""
    horse_id: str
    horse_name: str
    horse_number: int
    frame_number: int
    popularity: int
    running_style: str
    total_rating: int
    matched: list[RatingHit] = field(default_factory=list)
    last_finishing_position: int | None = None
    today_carry_weight: float | None = None
    rule24_active: bool = False  # F2 救済が走ったか
    # ルール評価の透明化(UI で「該当ルールなし」固定表示を避けるため)
    # evaluated_rule_ids: surface+距離区分が一致して「評価対象になった」ルール ID
    # missed_rule_ids:   evaluated だが going / 上3F / 通過順位で不発になった ID
    evaluated_rule_ids: list[str] = field(default_factory=list)
    missed_rule_ids: list[str] = field(default_factory=list)
    # v1.3: TARGET 指数(ZI、col[5])は **参考値** として保持するが
    # total_rating には含めない。UI で「参考: TARGET 指数 = X」表示用。
    target_index: int = 0
    # v1.9.1: 脚質判定の信頼度("high"/"medium"/"default")。default 値は "high"
    # で後方互換維持。後段で UI に「?」マーク表示やバックテスト分析に使う。
    running_style_confidence: str = "high"


# =====================================================================
# C / D / E 統合評価(過去走 1 行ごと)
# =====================================================================

# 全ての C/D/E ルールを 1 リストにまとめる(評価時に走査するため)
_ALL_CDE_RULES: list[RatingRule] = (
    RATING_RULES_C + RATING_RULES_D + RATING_RULES_E
)


def _evaluate_cde_for_run(
    run: dict,
    *,
    policy: str = DEFAULT_POLICY,
) -> list[tuple[RatingRule, str]]:
    """
    1 過去走に対して C/D/E すべてを評価し、policy に従って残すものを返す。

    戻り値: [(RatingRule, reason), ...] のリスト(0 件もあり得る)。
    """
    fired: list[tuple[RatingRule, str]] = []
    for r in _ALL_CDE_RULES:
        if r.spec is None:
            continue
        ok, reason = evaluate_rule(r.spec, run)
        if ok:
            fired.append((r, reason))

    if not fired:
        return []

    if policy == RatingPolicy.SUM_ALL:
        return fired

    # STRICT: C/D/E カテゴリは「過去走 1 行で最高 rate のもののみ採用」
    cde = [(r, reason) for r, reason in fired if r.category in ("C", "D", "E")]
    others = [(r, reason) for r, reason in fired if r.category not in ("C", "D", "E")]
    if cde:
        best = max(cde, key=lambda x: x[0].rate)
        return others + [best]
    return others


# =====================================================================
# F1 / F2 / F3 個別判定(当日コンテキスト依存)
# =====================================================================

def _check_f1_dirt_heavy_nigeru(
    race_meta: dict, running_style: str,
) -> tuple[bool, str]:
    """F1: ダ不良 + 逃げ → +30。"""
    surface = str(race_meta.get("surface", "")).strip()
    going = str(race_meta.get("going", "")).strip()
    if surface != "ダ" or going != "不良":
        return False, ""
    if running_style != "逃げ":
        return False, ""
    return True, "ダ不良 + 逃げ脚質"


def _check_f3_carry_weight_minus_3kg(
    today_carry_weight: float | None,
    prev_run: dict | None,
    today_distance: int,
) -> tuple[bool, str]:
    """F3: 1600m以上 + 斤量 -3kg(前走比)→ +20。"""
    if not today_distance or today_distance < 1600:
        return False, ""
    if today_carry_weight is None:
        return False, ""
    if prev_run is None:
        return False, ""
    prev_carry = prev_run.get("carry_weight")
    if prev_carry is None:
        return False, ""
    try:
        if pd.isna(prev_carry):
            return False, ""
        diff = float(today_carry_weight) - float(prev_carry)
    except (TypeError, ValueError):
        return False, ""
    if diff > -3.0:
        return False, ""
    return True, f"前走斤量 {prev_carry:.1f}kg → 今回 {today_carry_weight:.1f}kg ({diff:+.1f}kg)"


# =====================================================================
# メインエントリ: 1 頭分の rating 計算
# =====================================================================

def compute_horse_rating(
    *,
    horse_id: str,
    horse_name: str,
    horse_number: int,
    frame_number: int,
    popularity: int,
    running_style: str,
    last_finishing_position: int | None,
    today_carry_weight: float | None,
    past_runs: list[dict | None],
    race_meta: dict,
    policy: str = DEFAULT_POLICY,
    training_data: dict | None = None,
) -> HorseRating:
    """
    1 頭分の rating 計算を実行する。

    引数:
        horse_id, horse_name, horse_number, frame_number, popularity, running_style:
            馬の基本属性(prediction_logic 側で正規化済み)
        last_finishing_position: 前走着順(F2 / A3 等で参照)
        today_carry_weight: 当日斤量(F3 用、race_card_df から取得)
        past_runs: get_recent_runs_for_race の戻り値
                   [前走, 2走前, ..., 10走前](不足は None で末尾パディング)
                   v1.4 で 5 → 10 走に拡張。脚質判定は別経路で head5。
        race_meta: 当日レース情報(distance, surface, going, racecourse, race_number)
        policy: RatingPolicy.STRICT(デフォルト)or SUM_ALL
        training_data: v1.5 で追加。坂路調教 1F/2F の lap1/lap2 を持つ
                       dict(utils/training_data.match_training_to_horses の
                       1 馬分戻り値)。None なら F4/F5 は永続無効
                       (missed_rule_ids にだけ入る)。

    戻り値: HorseRating(total_rating, matched=list[RatingHit], ...)
    """
    matched: list[RatingHit] = []
    credited_rule_ids: set[str] = set()

    # F2 救済判定: 休養明け + 前走凡走 なら 2走前・3走前を「直近 2 走」として再評価
    rule24_active = detect_rule_24_situation(past_runs)

    if rule24_active:
        # 救済対象: 2走前 と 3走前(spec 通り、10走化でも救済範囲は変えない)
        target_pairs = [
            (1, past_runs[1] if len(past_runs) > 1 else None),
            (2, past_runs[2] if len(past_runs) > 2 else None),
        ]
    else:
        # 通常: v1.4 で 5 → 10 走に拡張(index 0..9)。
        # C/D/E/F1 は同 rule_id が複数走で発火しても dedup で 1 回まで。
        target_pairs = [
            (i, past_runs[i] if i < len(past_runs) else None)
            for i in range(10)
        ]

    # ----- C/D/E 評価(過去走ごと、policy に従って絞り込み + dedup) -----
    # evaluated_rule_ids: surface + 距離区分が一致して「評価対象になった」 ID。
    # 後で missed_rule_ids = evaluated - credited を算出して UI に表示する
    # (お父様向けに「該当ルールなし」固定でなく「試行したが不発」を見せる)。
    evaluated_rule_ids: set[str] = set()

    for run_idx, run in target_pairs:
        if run is None:
            continue

        # 「surface + 距離区分」が一致するルールを「評価対象」として集める
        run_surface = str(run.get("surface") or "").strip()
        try:
            run_distance = int(run["distance"]) if run.get("distance") else 0
        except (TypeError, ValueError):
            run_distance = 0
        if run_surface and run_distance > 0:
            for r in _ALL_CDE_RULES:
                if r.spec is None:
                    continue
                if r.spec.surface != run_surface:
                    continue
                try:
                    if r.spec.distance_match(run_distance):
                        evaluated_rule_ids.add(r.rule_id)
                except Exception:
                    pass

        kept = _evaluate_cde_for_run(run, policy=policy)
        for rule, reason in kept:
            if rule.rule_id in credited_rule_ids:
                continue  # 同 rule_id は 1 回まで
            matched.append(RatingHit(
                rule_id=rule.rule_id,
                rate=rule.rate,
                reason=f"{run_idx}走前: {reason}",
                run_idx=run_idx,
            ))
            credited_rule_ids.add(rule.rule_id)

    # ----- F2: 救済が発動 + 1 本でも C/D/E 該当 → +15 -----
    if rule24_active:
        any_cde_fired = any(
            hit.rule_id.startswith(("C", "D", "E"))
            for hit in matched
        )
        if any_cde_fired:
            matched.append(RatingHit(
                rule_id="F2",
                rate=15,
                reason="休養明け+前走凡走で救済評価が成立",
                run_idx=-1,
            ))
            credited_rule_ids.add("F2")

    # ----- F1: ダ不良 + 逃げ → +30 -----
    ok, reason = _check_f1_dirt_heavy_nigeru(race_meta, running_style)
    if ok and "F1" not in credited_rule_ids:
        matched.append(RatingHit(rule_id="F1", rate=30, reason=reason, run_idx=-1))
        credited_rule_ids.add("F1")

    # ----- F3: 1600m+ + 斤量 -3kg(前走比)→ +20 -----
    today_distance_raw = race_meta.get("distance", 0)
    try:
        today_distance = int(today_distance_raw) if today_distance_raw else 0
    except (TypeError, ValueError):
        today_distance = 0
    prev_run = past_runs[0] if past_runs else None
    ok, reason = _check_f3_carry_weight_minus_3kg(
        today_carry_weight, prev_run, today_distance,
    )
    if ok and "F3" not in credited_rule_ids:
        matched.append(RatingHit(rule_id="F3", rate=20, reason=reason, run_idx=-1))
        credited_rule_ids.add("F3")

    # ----- F4 / F5: 坂路調教(v1.5 で実装、v1.7.5 で閾値緩和) -----
    # training_data が None(坂路 CSV 未アップロード)→ 永続無効、ただし
    # missed_rule_ids には入れて UI で「データ未提供のためスキップ」と表示。
    # training_data あり → utils.training_data.evaluate_f4_f5 で発火判定。
    # F5 が発火するなら F4 は加算しない(F5 排他、+40 のみ)。
    #
    # v1.7.5.1: F4/F5 が credited されたら追加で穴馬ボーナスを評価
    # (人気 ≥ 6 番で F4穴 +15 / F5穴 +20)
    from utils.training_data import (  # 遅延 import で循環回避
        evaluate_f4_f5, evaluate_f4_f5_hole,
    )

    if training_data is None:
        # 坂路データ無し: 評価試行ログだけ残してスキップ
        evaluated_rule_ids.add("F4")
        evaluated_rule_ids.add("F5")
        evaluated_rule_ids.add("F4穴")
        evaluated_rule_ids.add("F5穴")
    else:
        f_rule, f_rate, f_reason = evaluate_f4_f5(training_data)
        evaluated_rule_ids.add("F4")
        evaluated_rule_ids.add("F5")
        evaluated_rule_ids.add("F4穴")
        evaluated_rule_ids.add("F5穴")
        if f_rule == "F5":
            matched.append(RatingHit(
                rule_id="F5", rate=f_rate, reason=f_reason, run_idx=-1,
            ))
            credited_rule_ids.add("F5")
        elif f_rule == "F4":
            matched.append(RatingHit(
                rule_id="F4", rate=f_rate, reason=f_reason, run_idx=-1,
            ))
            credited_rule_ids.add("F4")

        # 穴馬ボーナス(F4/F5 が credited された後で popularity ≥ 6 を判定)
        h_rule, h_rate, h_reason = evaluate_f4_f5_hole(f_rule, popularity)
        if h_rule:
            matched.append(RatingHit(
                rule_id=h_rule, rate=h_rate, reason=h_reason, run_idx=-1,
            ))
            credited_rule_ids.add(h_rule)

    # ----- C穴 / D穴 / E穴(v1.8.0 で導入したが第 1 案バックテストで悪化、撤回) -----
    # 第 1 案では C穴=15 / D穴=5 / E穴=10 を加点していたが、◎本命数が
    # 69 → 86 に増え穴馬が容易に rating ≥ 100 になりすぎ、複勝率が
    # 24.64% → 20.93% に悪化したため第 2 案で発火を停止する。
    # 評価試行ログ(missed_rule_ids)には残して UI で「v1.8.0 で撤回」を
    # 明示できるようにする。坂路の F4穴/F5穴 は維持(穴馬発見の主軸)。

    # ----- G カテゴリ: コース特性バイアス補正(v1.9.0 Phase 1、11 ルール) -----
    # コース × 枠順 × 脚質 のバイアス(東京ダ1400 外枠優位、新潟芝1000 直線
    # 外枠 36.1%、函館芝1200 逃げ 51.1% 等)を rating に反映。
    # G-Frame と G-Style から最大 1 つずつマッチ(1 馬最大 +20 程度)。
    # 配点は +5〜+12 の保守的範囲、減点なし(v1.8.0 の C穴 失敗を踏まえる)。
    from utils.course_bias_rules import evaluate_course_bias  # 遅延 import

    course = str(race_meta.get("racecourse") or "")
    surface = str(race_meta.get("surface") or "")
    try:
        distance = int(race_meta.get("distance") or 0)
    except (TypeError, ValueError):
        distance = 0
    style = str(running_style or "")
    # frame は引数 frame_number、style は引数 running_style から既に取得済
    if course and surface and distance > 0 and frame_number > 0:
        g_matches = evaluate_course_bias(
            course=course, surface=surface, distance=distance,
            frame=frame_number, style=style,
        )
        for g_rule in g_matches:
            if g_rule.rule_id in credited_rule_ids:
                continue
            matched.append(RatingHit(
                rule_id=g_rule.rule_id,
                rate=g_rule.rate,
                reason=g_rule.description,
                run_idx=-1,
            ))
            credited_rule_ids.add(g_rule.rule_id)
            evaluated_rule_ids.add(g_rule.rule_id)

    # ----- 合計 rating(contributes_to_rating=True のもののみ) -----
    total = 0
    for hit in matched:
        rule = find_rating_rule(hit.rule_id)
        if rule and rule.contributes_to_rating:
            total += hit.rate

    # 「試行したが不発」のルール = 評価対象 - 発火済
    missed = sorted(evaluated_rule_ids - credited_rule_ids)

    return HorseRating(
        horse_id=horse_id,
        horse_name=horse_name,
        horse_number=horse_number,
        frame_number=frame_number,
        popularity=popularity,
        running_style=running_style,
        total_rating=total,
        matched=matched,
        last_finishing_position=last_finishing_position,
        today_carry_weight=today_carry_weight,
        rule24_active=rule24_active,
        evaluated_rule_ids=sorted(evaluated_rule_ids),
        missed_rule_ids=missed,
    )


# =====================================================================
# レース全頭をまとめて評価するヘルパ(prediction_logic から呼ばれる)
# =====================================================================

def compute_ratings_for_race(
    horses_input: Iterable[dict],
    past_runs_by_horse: dict[str, list[dict | None]],
    race_meta: dict,
    policy: str = DEFAULT_POLICY,
) -> list[HorseRating]:
    """
    レース 1 つ分、全頭の rating を計算する。

    horses_input は prediction_logic 側で組み立てた dict のリスト想定。
    必要キー: horse_id, horse_name, horse_number, frame_number, popularity,
              running_style, last_finishing_position, today_carry_weight
    past_runs_by_horse[horse_id] が直近 10 走(v1.4、不足は None で末尾パディング)。
    """
    out: list[HorseRating] = []
    for h in horses_input:
        hid = str(h["horse_id"])
        runs = past_runs_by_horse.get(hid, [None] * 5)
        result = compute_horse_rating(
            horse_id=hid,
            horse_name=h["horse_name"],
            horse_number=h["horse_number"],
            frame_number=h["frame_number"],
            popularity=h["popularity"],
            running_style=h["running_style"],
            last_finishing_position=h.get("last_finishing_position"),
            today_carry_weight=h.get("today_carry_weight"),
            past_runs=runs,
            race_meta=race_meta,
            policy=policy,
        )
        out.append(result)
    return out
