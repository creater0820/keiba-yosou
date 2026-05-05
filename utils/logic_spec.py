"""
本ロジック v1.0 / ロジック仕様の単一情報源(SSoT: Single Source of Truth)。

CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」のルール群を、
UI 表示・トレーサビリティ用に構造化データとして保持する。

このファイルは「ルール定義 (CLAUDE.md) ↔ 実装 (utils/*.py) ↔ UI 表示」
の三者を一つに繋ぐ役割。CLAUDE.md を変更したらこちらも揃えて更新する。

利用箇所:
- pages/01_ロジック説明.py: ルール一覧と適用例を 2 カラムで表示
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ==================================================================
# データキャリア
# ==================================================================

@dataclass(frozen=True)
class LogicRule:
    """ロジック内 1 ルールの定義。"""
    rule_id: str            # "R9", "R24", "STEP2" 等(canonical ID)
    category: str           # "step1_onmark" / "step1_rest" / "step2" / "step3" / "step4" / "step5"
    title: str              # 1 行の見出し
    description: str        # 詳細(複数行 Markdown OK)
    code_refs: tuple[str, ...] = ()   # ("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=9)",)
    notes: str = ""         # 補足(挙動の特例・閾値・例など)


@dataclass(frozen=True)
class LogicCategory:
    """ルールのカテゴリ定義(表示順制御用)。"""
    key: str
    title: str
    summary: str
    rules: tuple[LogicRule, ...] = field(default_factory=tuple)


# ==================================================================
# Step 1: ○マーク収集ルール群(ルール 9〜22)
# ==================================================================

# 閾値テーブル(spec の数字をそのまま転記。実装側 RULES_9_TO_22 と完全一致)
_ONMARK_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="R9",
        category="step1_onmark",
        title="芝1400m以下・良 + 上3F<33.3 + 通過順位改善",
        description=(
            "芝の短距離 (≤1400m) を良馬場で走り、上がり3Fが 33.3秒未満 で、"
            "かつ通過順位がレース後半にかけて改善している過去走があれば ○ +1。\n"
            "**特例**: 阪神・中山では閾値 33.5秒 まで緩める。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=9)",),
    ),
    LogicRule(
        rule_id="R10",
        category="step1_onmark",
        title="芝1400m以下・重 + 上3F<34.0 + 通過順位改善",
        description=(
            "芝の短距離 (≤1400m) を 重 or 不良 で走り、上がり3Fが 34.0秒未満 で、"
            "通過順位改善があれば ○ +1。\n"
            "**特例**: 阪神・中山では閾値 34.2秒。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=10)",),
    ),
    LogicRule(
        rule_id="R11",
        category="step1_onmark",
        title="芝1600m・良 + 上3F<34.2 + 通過順位改善",
        description="芝マイル(=1600m)良馬場で上がり3Fが 34.2秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=11)",),
    ),
    LogicRule(
        rule_id="R12",
        category="step1_onmark",
        title="芝1600m・重 + 上3F<35.0(通過順位改善は不要)",
        description=(
            "芝マイル(=1600m)・重 or 不良で上がり3Fが 35.0秒未満 → ○ +1。\n"
            "**仕様の特例**: このルールだけ通過順位改善は要求しない(spec 通り)。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=12)",),
    ),
    LogicRule(
        rule_id="R13",
        category="step1_onmark",
        title="芝1800〜2000m・良 + 上3F<34.0 + 通過順位改善",
        description=(
            "芝中距離 (1800〜2000m) 良馬場で上3Fが 34.0秒未満 + 通過順位改善 → ○ +1。\n"
            "**特例**: 阪神・中山では閾値 34.5秒。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=13)",),
    ),
    LogicRule(
        rule_id="R14",
        category="step1_onmark",
        title="芝1800〜2000m・重 + 上3F<35.0 + 通過順位改善",
        description=(
            "芝中距離・重 or 不良で上3Fが 35.0秒未満 + 通過順位改善 → ○ +1。\n"
            "**特例**: 阪神・中山では閾値 35.5秒。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=14)",),
    ),
    LogicRule(
        rule_id="R15",
        category="step1_onmark",
        title="芝2200m以上・良 + 上3F<35.0 + 通過順位改善",
        description=(
            "芝長距離 (≥2200m) 良馬場で上3Fが 35.0秒未満 + 通過順位改善 → ○ +1。\n"
            "**特例**: 阪神・中山では閾値 35.5秒。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=15)",),
    ),
    LogicRule(
        rule_id="R16",
        category="step1_onmark",
        title="芝2200m以上・重 + 上3F<35.5 + 通過順位改善",
        description=(
            "芝長距離・重 or 不良で上3Fが 35.5秒未満 + 通過順位改善 → ○ +1。\n"
            "**特例**: 阪神・中山では閾値 36.0秒。"
        ),
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=16)",),
    ),
    LogicRule(
        rule_id="R17",
        category="step1_onmark",
        title="ダ1400m以下・良 + 上3F<35.0 + 通過順位改善",
        description="ダート短距離 (≤1400m) 良馬場で上3Fが 35.0秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=17)",),
    ),
    LogicRule(
        rule_id="R18",
        category="step1_onmark",
        title="ダ1400m以下・重 + 上3F<36.0 + 通過順位改善",
        description="ダート短距離・重 or 不良で上3Fが 36.0秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=18)",),
    ),
    LogicRule(
        rule_id="R19",
        category="step1_onmark",
        title="ダ1600〜2000m・良 + 上3F<36.0 + 通過順位改善",
        description="ダート中距離 (1600〜2000m) 良馬場で上3Fが 36.0秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=19)",),
    ),
    LogicRule(
        rule_id="R20",
        category="step1_onmark",
        title="ダ1600〜2000m・重 + 上3F<35.5 + 通過順位改善",
        description="ダート中距離・重 or 不良で上3Fが 35.5秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=20)",),
    ),
    LogicRule(
        rule_id="R21",
        category="step1_onmark",
        title="ダ2200m以上・良 + 上3F<37.0 + 通過順位改善",
        description="ダート長距離 (≥2200m) 良馬場で上3Fが 37.0秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=21)",),
    ),
    LogicRule(
        rule_id="R22",
        category="step1_onmark",
        title="ダ2200m以上・重 + 上3F<36.5 + 通過順位改善",
        description="ダート長距離・重 or 不良で上3Fが 36.5秒未満 + 通過順位改善 → ○ +1。",
        code_refs=("utils/onmark_rules.py:RULES_9_TO_22 (rule_no=22)",),
    ),
)


# ==================================================================
# Step 1.b: 休養明け救済(ルール24)
# ==================================================================

_REST_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="R24",
        category="step1_rest",
        title="休養明け前走凡走 → 2・3走前で評価する救済ルール",
        description=(
            "**「休養明け」 = 前走と前々走の race_date 差が 180日以上**\n"
            "前走が 5着以下(凡走)の場合、前走を評価対象から外し、"
            "2走前 と 3走前 のレースに対して通常の上3Fルール (R9〜R22) を適用する。\n"
            "復帰戦で結果を出せなかった休養明けの実力馬を救済する目的。"
        ),
        code_refs=(
            "utils/onmark_rules.py:detect_rule_24_situation",
            "utils/onmark_rules.py:collect_onmarks (R24 分岐)",
        ),
        notes="閾値: 180日以上 / 5着以下",
    ),
)


# ==================================================================
# Step 2: ◎本命判定
# ==================================================================

_JUDGMENT_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="STEP2-A",
        category="step2",
        title="◎本命候補 = ○マーク 5個以上",
        description=(
            "Step 1 の ○ 集計後、**○ ≥ 5 個** の馬を本命候補とする。\n"
            "複数いる場合は人気昇順(1番人気優先)でタイブレーク。"
        ),
        code_refs=(
            "utils/judgment_engine.py:HONMEI_MARK_THRESHOLD = 5",
            "utils/judgment_engine.py:determine_main_pick",
        ),
        notes="閾値は HONMEI_MARK_THRESHOLD で可変。長距離G1で出にくい場合は 3〜4 への引き下げ提案あり。",
    ),
    LogicRule(
        rule_id="STEP2-B",
        category="step2",
        title="◎候補なし → 準◎(最高○マーク数)を立てる",
        description=(
            "○≥5 の本命候補がいない、または減点で全候補が除外された場合、"
            "残った馬の中で最高の○マーク数を持つ馬を **準◎ (sub_pick)** として返す。\n"
            "spec の「◎なし(後述の減点・補正後に再判定)」の運用解釈。"
        ),
        code_refs=("utils/judgment_engine.py:determine_main_pick (sub_pick fallback)",),
    ),
)


# ==================================================================
# Step 3: 減点・除外ルール
# ==================================================================

_DEMERIT_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="R6",
        category="step3",
        title="単勝1番人気 + 逃げ脚質 → 2着以下扱い",
        description=(
            "1番人気の逃げ馬は他馬から狙われやすく、軸馬(◎)には不適。\n"
            "本命候補から除外し、2着以下扱い(3着候補にはなり得る)。"
        ),
        code_refs=("utils/judgment_engine.py:detect_demerit_horses (rule_6)",),
    ),
    LogicRule(
        rule_id="R7",
        category="step3",
        title="阪神1600m + 7枠 or 8枠 → 3着以下扱い",
        description=(
            "阪神コース1600m における外枠(7枠・8枠)は実績が薄く、軸馬には不適。\n"
            "3着以下扱い。"
        ),
        code_refs=("utils/judgment_engine.py:detect_demerit_horses (rule_7)",),
    ),
)


# ==================================================================
# Step 4: ワイド候補抽出
# ==================================================================

_WIDE_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="R3",
        category="step4",
        title="単勝1番人気の隣枠 + 7番人気以降 → ワイド候補",
        description=(
            "1番人気が入っている枠の **隣枠** にいる馬で、かつ 7番人気以降の伏兵 → ワイド候補。\n"
            "枠の風水的バランス + 妙味のある低人気馬を拾うルール。"
        ),
        code_refs=("utils/judgment_engine.py:extract_wide_candidates (rule_3)",),
    ),
    LogicRule(
        rule_id="R4",
        category="step4",
        title="前走着順が 5・7・9・11・13着 → ワイド候補",
        description=(
            "前走着順が「奇数着の中位〜後方」(5/7/9/11/13着)の馬 → ワイド候補。\n"
            "勝ち切れていないが穴を空ける可能性のある馬群を拾うルール。"
        ),
        code_refs=("utils/judgment_engine.py:extract_wide_candidates (rule_4)",),
    ),
    LogicRule(
        rule_id="R5",
        category="step4",
        title="1枠の逃げ馬 + 5番人気以降 → ワイド候補",
        description=(
            "1枠は内ラチ沿いを取れる絶好枠。逃げ脚質 + 5番人気以降の伏兵なら、"
            "粘り込みが期待できる → ワイド候補。"
        ),
        code_refs=("utils/judgment_engine.py:extract_wide_candidates (rule_5)",),
    ),
    LogicRule(
        rule_id="R8",
        category="step4",
        title="競馬場が小倉 or 中京 + 逃げ脚質 → ワイド候補",
        description=(
            "小倉・中京は逃げ馬がそのまま残りやすいバイアスがある。"
            "逃げ脚質の馬 → ワイド候補(人気不問)。"
        ),
        code_refs=("utils/judgment_engine.py:extract_wide_candidates (rule_8)",),
    ),
)


# ==================================================================
# Step 5: 買い目戦略・補正
# ==================================================================

_BETTING_RULES: tuple[LogicRule, ...] = (
    LogicRule(
        rule_id="R2",
        category="step5",
        title="1番人気の枠の偶奇でワイド候補を絞り込む",
        description=(
            "1番人気が **奇数枠 (1,3,5,7)** → 残すワイド候補は **奇数枠** のみ。\n"
            "1番人気が **偶数枠 (2,4,6,8)** → 残すワイド候補は **偶数枠** のみ。\n"
            "枠の偶奇バランスを使った絞り込み。"
        ),
        code_refs=("utils/betting_strategy.py:filter_by_frame_parity",),
    ),
    LogicRule(
        rule_id="R23",
        category="step5",
        title="ダート + 不良馬場 → 逃げ馬に ○ +1 補正",
        description=(
            "**ダート + 不良馬場** のレースでは逃げ脚質の馬に ○ を 1 個追加加算する。\n"
            "馬場が荒れた時の逃げ馬の信頼度を Step 1 の ○ に上乗せして補正する。"
        ),
        code_refs=("utils/betting_strategy.py:apply_dirt_heavy_correction",),
        notes="加算は Step 2 の本命判定より前に走る(○≥5 達成しやすくなる効果も)。",
    ),
)


# ==================================================================
# 補足: 脚質判定基準
# ==================================================================

RUNNING_STYLE_SPEC = LogicRule(
    rule_id="STYLE",
    category="meta",
    title="脚質の判定基準(過去5走の初角通過順位の平均)",
    description=(
        "過去5走の **初角(=corner_1)通過順位** の平均値で脚質を分類:\n"
        "- 平均 1〜3番手  → **逃げ**\n"
        "- 平均 4〜6番手  → **先行**\n"
        "- 平均 7〜10番手 → **差し**\n"
        "- 平均 11番手以下 → **追込**\n"
        "- 過去走 3走未満  → **不明(暫定で先行扱い)**"
    ),
    code_refs=("utils/race_history.py:determine_running_style",),
)


# ==================================================================
# カテゴリ表(表示順制御)
# ==================================================================

LOGIC_CATEGORIES: tuple[LogicCategory, ...] = (
    LogicCategory(
        key="step1_onmark",
        title="Step 1: ○マーク収集(R9〜R22)",
        summary="過去走の上がり3F + 通過順位改善 で芝/ダ × 距離 × 良/重 の14ルールを評価。",
        rules=_ONMARK_RULES,
    ),
    LogicCategory(
        key="step1_rest",
        title="Step 1.b: 休養明け救済(R24)",
        summary="休養明けで前走凡走の馬は前走を無視して 2・3走前で評価する救済。",
        rules=_REST_RULES,
    ),
    LogicCategory(
        key="step2",
        title="Step 2: ◎本命判定",
        summary="○ ≥ 5 を本命候補に。候補ゼロ時は最高○の馬を準◎にフォールバック。",
        rules=_JUDGMENT_RULES,
    ),
    LogicCategory(
        key="step3",
        title="Step 3: 減点・除外(R6, R7)",
        summary="軸馬には不適な状況(1番人気逃げ・阪神マイル外枠)を本命から外す。",
        rules=_DEMERIT_RULES,
    ),
    LogicCategory(
        key="step4",
        title="Step 4: ワイド候補抽出(R3, R4, R5, R8)",
        summary="◎とは別軸で、人気薄・伏兵で穴を空けやすい馬を最大3頭まで抽出。",
        rules=_WIDE_RULES,
    ),
    LogicCategory(
        key="step5",
        title="Step 5: 買い目戦略・補正(R2, R23)",
        summary="ダート不良で逃げ馬に加点。1番人気の枠偶奇でワイド候補を絞る。",
        rules=_BETTING_RULES,
    ),
)


def all_rules() -> tuple[LogicRule, ...]:
    """全ルールをフラットに取得(rule_id 検索用)。"""
    out: list[LogicRule] = []
    for cat in LOGIC_CATEGORIES:
        out.extend(cat.rules)
    out.append(RUNNING_STYLE_SPEC)
    return tuple(out)


def find_rule(rule_id: str) -> LogicRule | None:
    """rule_id ("R9" / "STEP2-A" 等)からルール定義を引く。"""
    for r in all_rules():
        if r.rule_id == rule_id:
            return r
    return None
