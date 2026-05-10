"""
本ロジック v1.0 のロジック仕様を可視化するページ。

構成(上から):
  1) ロジック全体の 5 ステップ概要
  2) レース選択(セッション内 or テストデータの morning_race_card)
  3) 2 カラム本体:
        左 = ルール定義(CLAUDE.md / utils/logic_spec.py の SSoT)
        右 = 実データ適用例(選択中レース or 全レース横断)
  4) ◎ が出にくい問題の可視化(○マーク分布 + 本命確定率)

実装の単一情報源:
- ルール定義: utils/logic_spec.py
- 適用例の集計: utils/logic_examples.py
- 予想結果: prediction_logic.predict_all_races_v1
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from data_loader import HistoricalData, load_historical_data, load_race_card
from prediction_logic import RacePrediction, predict_all_races_v1
from utils.judgment_engine import HONMEI_MARK_THRESHOLD
from utils.logic_examples import (
    MarksDistribution,
    RuleApplication,
    collect_applications_for_race,
    compute_marks_distribution,
    index_by_rule,
)
from utils.logic_spec import (
    LOGIC_CATEGORIES,
    RUNNING_STYLE_SPEC,
    LogicCategory,
    LogicRule,
)
from utils.rating_rules import (
    ALL_RATING_RULES,
    HONMEI_RATING_THRESHOLD,
    RATING_RULES_A,
    RATING_RULES_B,
    RATING_RULES_C,
    RATING_RULES_D,
    RATING_RULES_E,
    RATING_RULES_F,
)


# =====================================================================
# ページ設定
# =====================================================================
st.set_page_config(
    page_title="ロジック説明 — 本ロジック v1.4",
    page_icon="🧭",
    layout="wide",
)


# =====================================================================
# データソース選択(セッション内 → テストデータ自動ロード)
# =====================================================================

TEST_RACE_CARD_PATH = Path("data/test/morning_race_card_20260503.csv")


@st.cache_data(show_spinner="テスト用出馬表を読み込み中…")
def _load_test_race_card() -> pd.DataFrame:
    """data/test/morning_race_card_20260503.csv を読み込む。"""
    raw = TEST_RACE_CARD_PATH.read_bytes()
    return load_race_card(io.BytesIO(raw))


@st.cache_data(show_spinner="過去データを読み込み中…")
def _load_historical_for_page() -> HistoricalData:
    return load_historical_data()


@st.cache_data(show_spinner="テストデータで予想計算中…")
def _predict_test_data(mode: str) -> dict[str, RacePrediction]:
    """テストデータの morning race card を指定モードで予想する。"""
    rc = _load_test_race_card()
    hist = _load_historical_for_page()
    return predict_all_races_v1(rc, hist, mode=mode)


def _get_predictions(mode: str) -> tuple[dict[str, RacePrediction], str]:
    """予想結果と「データ出所」のラベルを返す。

    セッションの予想結果は app.py で計算されたモード固定。
    ここで requested mode と互換でなければテストデータから再計算する。

    モード互換マップ:
      - logic_mode == "rating"  : "rating" トグルでのみ採用
      - logic_mode == "onmark"  : "onmark" トグルでのみ採用
      - logic_mode == "dc"      : 内部で rating ロジック(C/D/E/F)を使うため
                                  "rating" トグルで採用(主用途のお父様の DC で
                                  実 CSV がロジック説明に反映されないバグの修正)
    """
    in_session = st.session_state.get("all_predictions")
    if in_session:
        sample = next(iter(in_session.values()), None)
        sess_mode = getattr(sample, "logic_mode", "onmark") if sample else "onmark"

        # DC 形式は rating ロジックの上位適用なので "rating" トグルと互換
        compatible = (
            sess_mode == mode
            or (sess_mode == "dc" and mode == "rating")
        )
        if compatible:
            label_extra = "(DC 形式)" if sess_mode == "dc" else ""
            return in_session, f"セッション中の予想結果({mode} モード){label_extra}"

    if TEST_RACE_CARD_PATH.exists():
        try:
            preds = _predict_test_data(mode)
            return preds, f"テストデータ({mode} モード / {TEST_RACE_CARD_PATH.name})"
        except Exception as e:
            st.error(f"テストデータの読み込みに失敗: {e}")
            return {}, ""

    return {}, ""


# =====================================================================
# 描画ヘルパ
# =====================================================================

def _render_overview() -> None:
    """ロジック全体の 5 ステップ概要を描く。"""
    st.markdown(
        """
        本ロジック v1.0 は **5 ステップのエキスパートシステム** で構成されます。
        スコア値の単純なランキングではなく、複数のルールを多層的に適用して
        最終的な ◎本命 / ワイド候補 / 危険人気馬 / 推奨買い目 を生成します。

        ```
        Step 1  ○マーク収集     ← 過去走 (R9〜R22 + R24)
           │
        Step 2  ◎本命判定        ← ○ ≥ 5 + タイブレーク
           │
        Step 3  減点・除外        ← R6 (1番人気逃げ) / R7 (阪神1600外枠)
           │
        Step 4  ワイド候補抽出   ← R3 / R4 / R5 / R8(最大3頭)
           │
        Step 5  買い目戦略・補正 ← R2 (枠偶奇) / R23 (ダート不良補正)
        ```
        """
    )


def _render_rule_card(rule: LogicRule) -> None:
    """ルール 1 件を expander で描く(左カラム用)。"""
    with st.expander(f"**{rule.rule_id}** — {rule.title}", expanded=False):
        st.markdown(rule.description)
        if rule.notes:
            st.caption(f"📌 補足: {rule.notes}")
        if rule.code_refs:
            st.caption("**実装(トレーサビリティ)**:")
            for ref in rule.code_refs:
                st.code(ref, language="text")


def _render_left_column(categories: tuple[LogicCategory, ...]) -> None:
    """左カラム: 全ルール定義(カテゴリ順)を描く。"""
    st.markdown("### 📚 ルール定義(CLAUDE.md より)")
    st.caption("SSoT: `utils/logic_spec.py` / 実装: `utils/{onmark_rules,judgment_engine,betting_strategy}.py`")
    for cat in categories:
        st.markdown(f"#### {cat.title}")
        st.caption(cat.summary)
        for rule in cat.rules:
            _render_rule_card(rule)
        st.markdown("")  # spacing

    # 補足: 脚質判定基準
    st.markdown("#### 🐎 補足: 脚質判定")
    _render_rule_card(RUNNING_STYLE_SPEC)


def _race_name_or_empty(name: str | None) -> str:
    """v1.4: race_name が空 / "(レース名不明)" 等なら空文字を返す。"""
    if not name:
        return ""
    s = str(name).strip()
    if not s or "不明" in s:
        return ""
    return s


def _format_application_line(app: RuleApplication, *, show_race: bool = True) -> str:
    """RuleApplication 1件を 1 行マークダウンに整形。"""
    head = f"馬番{app.horse_number} **{app.horse_name}**"
    if show_race:
        rn = _race_name_or_empty(app.race_name)
        suffix = f" {rn}" if rn else ""
        head += f"  〔{app.racecourse}{app.race_number}R{suffix}〕"
    return f"- {head}\n    - {app.detail}"


def _render_examples_for_race(
    selected_race: str | None,
    predictions: dict[str, RacePrediction],
    rule_index_all_races: dict[str, list[RuleApplication]],
    categories: tuple[LogicCategory, ...],
) -> None:
    """右カラム: 選択中レース or 全レース横断で各ルールの該当馬を表示。"""
    st.markdown("### 🎯 実データへの適用例")
    if not predictions:
        st.warning("予想結果が空です。app.py で出馬表をアップロードして「予想実行」してください。")
        return

    if selected_race and selected_race in predictions:
        # 選択レースに限定して描く
        pred = predictions[selected_race]
        rule_index = {}
        for app in collect_applications_for_race(pred):
            rule_index.setdefault(app.rule_id, []).append(app)
        rn = _race_name_or_empty(pred.race_meta.get("race_name"))
        rn_part = f" {rn}" if rn else ""
        scope_label = (
            f"{pred.race_meta.get('racecourse','')} "
            f"{pred.race_meta.get('race_number','')}R{rn_part}"
        )
        st.caption(f"対象: **{scope_label}**(レース限定表示)")
        show_race = False  # レース固定なので馬名行に場・R を出さない
    else:
        # 全レース横断
        rule_index = rule_index_all_races
        st.caption(f"対象: **全 {len(predictions)} レース横断**")
        show_race = True

    # カテゴリ順に「該当ルール → 馬」を出す
    for cat in categories:
        any_in_cat = any(rule.rule_id in rule_index for rule in cat.rules)
        st.markdown(f"#### {cat.title}")
        if not any_in_cat:
            st.caption("(このカテゴリで該当馬なし)")
            continue
        for rule in cat.rules:
            apps = rule_index.get(rule.rule_id, [])
            if not apps:
                with st.expander(f"**{rule.rule_id}** — 該当 0 頭", expanded=False):
                    st.caption("このスコープでは該当馬なし")
                continue
            with st.expander(
                f"**{rule.rule_id}** — 該当 {len(apps)} 頭", expanded=False
            ):
                for app in apps:
                    st.markdown(_format_application_line(app, show_race=show_race))


def _render_marks_distribution_section(dist: MarksDistribution) -> None:
    """◎が出にくい問題の可視化:○マーク分布 + 本命確定率。"""
    st.markdown("---")
    st.markdown("### 📈 ◎ 確定率の可視化(○マーク閾値の妥当性チェック)")

    col_metric1, col_metric2, col_metric3 = st.columns(3)
    main_rate = (
        dist.races_with_main_pick / dist.races_total * 100
        if dist.races_total else 0
    )
    sub_only_rate = (
        dist.races_with_sub_pick_only / dist.races_total * 100
        if dist.races_total else 0
    )
    col_metric1.metric(
        "◎本命確定レース",
        f"{dist.races_with_main_pick} / {dist.races_total}",
        f"{main_rate:.1f}%",
    )
    col_metric2.metric(
        "準◎ fallback のみ",
        f"{dist.races_with_sub_pick_only} / {dist.races_total}",
        f"{sub_only_rate:.1f}%",
    )
    col_metric3.metric(
        "現在の本命閾値",
        f"○ ≥ {HONMEI_MARK_THRESHOLD}",
        help="utils/judgment_engine.py:HONMEI_MARK_THRESHOLD で可変",
    )

    # ヒストグラム(○マーク数 → 頭数)
    hist_rows = []
    max_mark = max(dist.histogram.keys() or [0])
    for marks in range(0, max_mark + 1):
        count = dist.histogram.get(marks, 0)
        hist_rows.append({"○マーク数": marks, "馬の数": count})
    hist_df = pd.DataFrame(hist_rows)
    st.markdown("#### 出走馬全頭の ○マーク分布")
    st.bar_chart(hist_df.set_index("○マーク数"), height=200)

    # 本命候補(○≥5)の該当馬を全レース横断で出す
    st.markdown("#### 各レースの最大○マーク数")
    st.caption("○ < 閾値 のレースは準◎ fallback で運用される。閾値引き下げの判断材料。")
    rows_max = [
        {"race_id": rid, "最大○": m}
        for rid, m in sorted(dist.max_marks_per_race.items(), key=lambda x: -x[1])
    ]
    if rows_max:
        st.dataframe(
            pd.DataFrame(rows_max).head(40),
            hide_index=True,
            use_container_width=True,
            height=300,
        )


# =====================================================================
# メインレンダリング
# =====================================================================

st.title("🧭 本ロジック ロジック説明")
st.caption(
    "CLAUDE.md「推奨馬選定ロジック」の全ルールを、"
    "実データ(出馬表 + 過去走 Parquet)への適用結果と並べて表示します。"
)

# ----- ロジックモード切替トグル -----
mode = st.radio(
    "ロジックモード",
    options=["rating", "onmark"],
    format_func=lambda x: {
        "rating": "🆕 v1.4 — レーティング合計(≥100 で ◎、直近10走評価)",
        "onmark": "📜 v1.0 — ○マーク数(≥5 で ◎、直近5走評価)",
    }[x],
    horizontal=True,
    index=0,
)

if mode == "rating":
    st.markdown("""
    **本ロジック v1.4 (rating-based + 直近10走評価)** の概要:

    各馬の **直近10走** を評価し、各カテゴリのルールが該当するごとに rate を加算する。
    合計 rating が **100 点以上** で ◎本命確定。100 点未満なら最高 rating を準◎にする。

    > ※ **ルール評価は直近10走、脚質判定は直近5走の corner_1 平均**
    > (脚質は直近の傾向を見るため意図的に短めに保持)。
    > 直近 N 走戦歴マトリクス UI も表示は 5 走のまま(画面幅・お父様の慣れ)。

    | カテゴリ | 内容 | rate |
    |---|---|---|
    | **C** (14 ルール) | 距離×馬場 別 上3F+通過順位改善 | 50 |
    | **D** (4 ルール) | 距離無関係 上3F+通過順位改善 | 20 |
    | **E** (14 ルール) | 距離×馬場 別 上3F のみ(通過順位改善 不要) | 20 |
    | **F1** | ダート不良 + 逃げ脚質 | 30 |
    | **F2** | 休養明け前走凡走 → 2,3走前で C/D/E 救済 | 15 |
    | **F3** | 1600m以上 + 斤量 -3kg(前走比) | 20 |
    | **F4** | 坂路 好調(1F ≤ 12.5 OR 1F+2F ≤ 25.4)| 30 |
    | **F5** | 坂路 抜群(1F ≤ 12.3 OR 1F+2F ≤ 24.8、F4 排他)| 40 |
    | **F4穴** | F4 該当 + 人気 ≥ 6 番(穴馬上積み)| +15 |
    | **F5穴** | F5 該当 + 人気 ≥ 6 番(穴馬上積み)| +20 |
    | **A2-A5** | ワイド候補フラグ(rating には不加算、priority weight) | 4-30 |
    | **B1-B2** | 減点フラグ(rating には不加算、◎候補から除外) | - |

    **重複処理 (RatingPolicy.STRICT)**:
    - 過去走 1 行で C/D/E が複数該当 → 最高 rate のみ採用(over-counting 防止)
    - 同 rule_id が複数走で発火 → 1 回まで(dedup)

    実装: `utils/rating_rules.py` (SSoT) + `utils/rating_engine.py` (計算)
    """)
else:
    _render_overview()

predictions, source_label = _get_predictions(mode)

if not predictions:
    st.info(
        "予想結果が利用できません。\n"
        "- app.py で出馬表をアップロード → 予想実行 すると、ここでもセッション内の結果が見られます\n"
        "- それまでは `data/test/morning_race_card_20260503.csv` のテストデータが自動表示されます"
    )
    st.stop()

st.success(f"📦 データ出所: {source_label}")

# ----- レース選択 -----
race_options = ["(全レース横断)"]
race_label_to_id: dict[str, str] = {}
for rid, p in predictions.items():
    m = p.race_meta
    rn = _race_name_or_empty(m.get("race_name"))
    rn_part = f" {rn}" if rn else ""
    label = (
        f"{m.get('racecourse','')} {m.get('race_number','')}R"
        f"{rn_part}"
        f"  〔{m.get('distance','')}m {m.get('surface','')} / {m.get('going','')}〕"
    )
    race_options.append(label)
    race_label_to_id[label] = rid

# 京都11R(天皇賞春)があればデフォルトに(検証で使った既知レース)
default_idx = 0
for i, label in enumerate(race_options):
    if "京都" in label and "11R" in label:
        default_idx = i
        break

selected_label = st.selectbox(
    "対象レースを選ぶ(右カラムの適用例の絞り込みに使う)",
    race_options,
    index=default_idx,
)
selected_race_id = race_label_to_id.get(selected_label)  # 全レース横断なら None

if mode == "onmark":
    # ===== v1.0 (○マーク方式) ビュー =====
    rule_index_all = index_by_rule(predictions)
    col_left, col_right = st.columns([1, 1])
    with col_left:
        _render_left_column(LOGIC_CATEGORIES)
    with col_right:
        _render_examples_for_race(
            selected_race=selected_race_id,
            predictions=predictions,
            rule_index_all_races=rule_index_all,
            categories=LOGIC_CATEGORIES,
        )
    dist = compute_marks_distribution(predictions)
    _render_marks_distribution_section(dist)

else:
    # ===== v1.1 (rating-based) ビュー =====
    col_left, col_right = st.columns([1, 1])
    with col_left:
        st.markdown("### 📚 ルール一覧(全 39 ルール)")
        st.caption("SSoT: `utils/rating_rules.py` / 計算: `utils/rating_engine.py`")
        for cat_label, rules in [
            ("【C】距離×馬場 上3F+通過順位改善 (rate 50)", RATING_RULES_C),
            ("【D】距離無関係 上3F+通過順位改善 (rate 20)", RATING_RULES_D),
            ("【E】距離×馬場 上3F のみ (rate 20)", RATING_RULES_E),
            ("【F】補正・特殊条件", RATING_RULES_F),
            ("【A】戦略・ワイド候補(rating 不加算)", RATING_RULES_A),
            ("【B】減点(rating 不加算、◎除外)", RATING_RULES_B),
        ]:
            with st.expander(cat_label, expanded=False):
                rows = []
                for r in rules:
                    rows.append({
                        "ID": r.rule_id,
                        "rate": r.rate,
                        "rating加算": "○" if r.contributes_to_rating else "—",
                        "有効": "○" if r.enabled else "TODO",
                        "概要": r.title,
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with col_right:
        st.markdown("### 🎯 実データへの適用例")
        if selected_race_id and selected_race_id in predictions:
            pred = predictions[selected_race_id]
            ratings_sorted = sorted(
                pred.horse_ratings, key=lambda r: -r.total_rating,
            )
            rn = _race_name_or_empty(pred.race_meta.get("race_name"))
            rn_part = f" {rn}" if rn else ""
            scope_label = (
                f"{pred.race_meta.get('racecourse','')} "
                f"{pred.race_meta.get('race_number','')}R{rn_part}"
            )
            st.caption(f"対象: **{scope_label}**(レース限定表示)")

            # 軸馬の判定
            j = pred.judgment
            if j.main_pick:
                axis_h = next((r for r in ratings_sorted if r.horse_id == j.main_pick), None)
                if axis_h:
                    st.success(
                        f"◎本命: 馬番{axis_h.horse_number} {axis_h.horse_name} "
                        f"(rating {axis_h.total_rating} / {axis_h.popularity}人気)"
                    )
            elif j.sub_pick:
                axis_h = next((r for r in ratings_sorted if r.horse_id == j.sub_pick), None)
                if axis_h:
                    st.warning(
                        f"準◎: 馬番{axis_h.horse_number} {axis_h.horse_name} "
                        f"(rating {axis_h.total_rating} / {axis_h.popularity}人気)"
                    )

            # 全頭ランキング
            for r in ratings_sorted:
                head = (
                    f"馬番{r.horse_number} {r.horse_name} "
                    f"({r.popularity}人気 / {r.running_style}) "
                    f"— **rating {r.total_rating}**"
                )
                if r.matched:
                    with st.expander(head, expanded=False):
                        for hit in r.matched:
                            st.write(f"- **{hit.rule_id}** (+{hit.rate}): {hit.reason}")
                        if r.rule24_active:
                            st.caption("📌 F2 救済発動 (2,3走前で評価)")
                else:
                    st.write(head + "  — 該当ルールなし")
        else:
            st.caption(f"対象: **全 {len(predictions)} レース横断**")
            # 各レースの最高 rating + 本命確定状況
            rows = []
            for rid, pred in predictions.items():
                meta = pred.race_meta
                top_h = max(pred.horse_ratings, key=lambda r: r.total_rating, default=None)
                if not top_h:
                    continue
                rows.append({
                    "レース": f"{meta.get('racecourse','')}{meta.get('race_number','')}R",
                    "最高rating": top_h.total_rating,
                    "馬名": top_h.horse_name,
                    "確定": "◎" if pred.judgment.main_pick else ("準◎" if pred.judgment.sub_pick else "—"),
                })
            rows.sort(key=lambda r: -r["最高rating"])
            st.dataframe(pd.DataFrame(rows).head(40), hide_index=True, use_container_width=True)

    # ----- 確定率の可視化(rating 版) -----
    st.markdown("---")
    st.markdown("### 📈 ◎ 確定率の可視化(rating ≥ 100 のレース統計)")

    n_main = sum(1 for p in predictions.values() if p.judgment.main_pick)
    n_sub = sum(1 for p in predictions.values() if p.judgment.sub_pick and not p.judgment.main_pick)
    main_rate = n_main / len(predictions) * 100 if predictions else 0

    col_a, col_b, col_c = st.columns(3)
    col_a.metric(
        "◎本命確定レース",
        f"{n_main} / {len(predictions)}",
        f"{main_rate:.1f}%",
    )
    col_b.metric(
        "準◎ fallback のみ",
        f"{n_sub} / {len(predictions)}",
        f"{n_sub / len(predictions) * 100:.1f}%" if predictions else "0%",
    )
    col_c.metric(
        "本命閾値",
        f"rating ≥ {HONMEI_RATING_THRESHOLD}",
        help="utils/rating_rules.py:HONMEI_RATING_THRESHOLD で可変",
    )

    # rating ヒストグラム(全頭)
    rating_buckets: dict[int, int] = {}
    for p in predictions.values():
        for r in p.horse_ratings:
            bucket = (r.total_rating // 20) * 20  # 20点刻み
            rating_buckets[bucket] = rating_buckets.get(bucket, 0) + 1
    if rating_buckets:
        max_b = max(rating_buckets.keys())
        rows = [
            {"rating帯(下限)": b, "馬の数": rating_buckets.get(b, 0)}
            for b in range(0, max_b + 20, 20)
        ]
        st.markdown("#### 全頭の rating 分布(20点刻み)")
        st.bar_chart(pd.DataFrame(rows).set_index("rating帯(下限)"), height=200)

    # 各レース最大 rating
    st.markdown("#### 各レースの最高 rating")
    rows_max = []
    for rid, p in predictions.items():
        m = p.race_meta
        top_h = max(p.horse_ratings, key=lambda r: r.total_rating, default=None)
        if not top_h:
            continue
        rows_max.append({
            "race_id": rid,
            "場": m.get("racecourse", ""),
            "R": m.get("race_number", 0),
            "最高rating": top_h.total_rating,
            "馬": top_h.horse_name,
            "確定": "◎" if p.judgment.main_pick else ("準◎" if p.judgment.sub_pick else "—"),
        })
    rows_max.sort(key=lambda r: -r["最高rating"])
    if rows_max:
        st.dataframe(
            pd.DataFrame(rows_max).head(40),
            hide_index=True, use_container_width=True, height=300,
        )
