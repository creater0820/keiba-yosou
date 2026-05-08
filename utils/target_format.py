"""
TARGET frontier JV (JRA-VAN DataLab) の RA+SE+単勝オッズ 結合 CSV を
共通パーサで扱うためのモジュール。

過去データ取り込み (scripts/csv_to_parquet.py) と
当日出馬表アップロード (data_loader.load_race_card) の両方から参照する。

フォーマット仕様:
- ヘッダー行なし
- 文字コード: Shift_JIS (cp932)
- 52列の位置依存フォーマット
- 結果列(着順 / タイム / 上がり3F 等)が空の "morning" 形式と、
  全列埋まっている過去データの両方に対応(欠損は NaN/空文字 のまま伝搬)
"""

from __future__ import annotations

import pandas as pd

# JV-Link 形式の期待される列数
JV_LINK_EXPECTED_COLS = 52

# 列インデックス → 内部フィールド名(本MVPで使う列のみ)
#
# 確定済みマッピング:
#   [0-2]   年・月・日
#   [3]     開催回
#   [4]     競馬場(漢字)
#   [5]     開催日次
#   [6]     レース番号    ← race_number
#   [7]     レース名
#   [8]     出走頭数
#   [9]     トラック種別(芝/ダ/障)
#   [10]    内/外
#   [11]    距離(m)
#   [12]    馬場状態
#   [13]    馬名
#   [14]    性別
#   [15]    年齢
#   [16]    騎手
#   [17]    斤量
#   [18]    出走頭数(全行同値)
#   [19]    馬番(1〜N、レース内ユニーク。JRA 公式の馬番と直接一致) ← horse_number
#            京都11R 天皇賞春の14/15頭(JRA listed 7頭中6頭)で照合済み。
#            CSV の行は [19] でソートされている。
#   [20]    着順(2桁ゼロ埋め、例 '01' = 1着)            ← finishing_position
#   [21]    着順(同 [20] の複製、JV-Link が冗長に出力)
#   [22-23] 着差・着差秒
#   [24]    単勝人気(1〜N、レース内ユニーク。1=最も人気) ← popularity
#            京都11R 天皇賞春で クロワデュノール=1 / アドマイヤテラ=2 /
#            ヴェルテンベルク=12 と JRA 公式と完全一致を確認済み。
#   [28]    1コーナー通過順位                          ← corner_1
#   [29]    2コーナー通過順位                          ← corner_2
#   [30]    3コーナー通過順位                          ← corner_3
#   [31]    4コーナー通過順位                          ← corner_4
#            (2025 ダービー クロワデュノール の 4-3-2-3 パターン
#             含め、複数レースの典型パターン(逃げ:1-1-1-1、追込:18-18-18-18
#             等)で実 JRA 結果と整合することを確認済み)
#   [25]    走破タイム(秒、例: 70.3 = 1分10秒3)
#   [26]    走破タイム(別表現、1103 = 1分10秒3)
#   [27-31] 時計指数・通過順
#   [32]    上がり3F(秒)
#   [33]    馬体重(kg)
#   [34]    調教師
#   [35]    厩舎所属(栗東/美浦)
#   [37]    血統登録番号(8桁、馬を stable に識別)  ← horse_id
#   [40]    出走エントリ通し番号(10桁、レース毎に増えるため馬同一性には使えない)
#   [43]    父、 [44] 母、 [45] 母父
#   [51]    単勝オッズ
RACES_COL: dict[str, int] = {
    "year":                0,
    "month":               1,
    "day":                 2,
    "racecourse":          4,
    "race_number":         6,
    "race_name":           7,
    "surface":             9,
    "distance":           11,
    "going":              12,
    "horse_name":         13,
    "jockey":             16,
    # 斤量 (kg、Phase 6.1 / rating rule F3 用)。column [17] = レース当日の斤量。
    "carry_weight":       17,
    # 真の着順は [20]([21] は完全複製)。複数 G1(大阪杯/東京優駿/皐月賞/JC/
    # ホープフル) でクロワデュノールの実 JRA 着順と一致することを確認済み。
    "finishing_position": 20,
    # 真の馬番は [19]。京都11R 天皇賞春で JRA 公式7頭中6頭と一致(他レースも
    # スポットチェック済み)。CSV の行はこの列でソートされている。
    # かつて [24] を馬番として使っていたが、それは「1..N の別の順位」で
    # 馬番ではなかった(クロワデュノール=1, マイネル=10 のみ偶然一致)。
    "horse_number":       19,
    "time_seconds":       25,
    "last_3f":            32,
    "weight":             33,
    "trainer":            34,
    # horse_id は [37] 血統登録番号(8桁、stable)。同じ馬が複数レースで同じ値を持つ。
    # かつて [40] を使っていたが per-race で変わる通し番号と判明 → 過去履歴の引き当てに使えなかった。
    "horse_id":           37,
    # 単勝人気(1=最も人気)。JRA 公式の人気と一致するのは [24] のみ。
    # かつて [24] を「馬番」「用途不明な順位」と誤分類していた経緯あり。
    "popularity":         24,
    # コーナー通過順位(1〜4 コーナー、本ロジック v1.0 の脚質判定で使う)
    "corner_1":           28,
    "corner_2":           29,
    "corner_3":           30,
    "corner_4":           31,
    "sire":               43,
    "dam":                44,
    "dam_sire":           45,
    "odds":               51,
}

# 検証用 JRA中央10場
KNOWN_COURSES: set[str] = {
    "東京", "中山", "京都", "阪神", "小倉", "福島", "新潟", "函館", "札幌", "中京",
}

# 発走時刻(レース番号 → "HH:MM")の推定テーブル。
# TARGET frontier JV の「フルセット+単勝オッズ」エクスポートには発走時刻列が
# 含まれていないため、JRAの典型的なタイムスケジュールから推定する。
# 競馬場・日付によって ±10分程度ズレることがある(注記つきで表示する)。
ESTIMATED_POST_TIMES_BY_RACE_NUMBER: dict[int, str] = {
    1:  "10:00",
    2:  "10:30",
    3:  "11:00",
    4:  "11:30",
    # 4R - 5R 間に昼休憩(60 分前後)
    5:  "12:30",
    6:  "13:00",
    7:  "13:30",
    8:  "14:00",
    9:  "14:30",
    10: "15:00",
    # 11R は重賞・G1 で 15:40 になることが多い
    11: "15:35",
    12: "16:05",
}


def estimate_post_time(race_number) -> str:
    """レース番号から発走時刻(HH:MM)を推定する。範囲外/欠損なら空文字を返す。"""
    try:
        if pd.isna(race_number):
            return ""
        n = int(race_number)
    except (ValueError, TypeError):
        return ""
    return ESTIMATED_POST_TIMES_BY_RACE_NUMBER.get(n, "")


# =====================================================================
# 単純ヘルパ
# =====================================================================

def secs_to_time_str(secs) -> str:
    """秒数を 'M:SS.SS' 形式に変換(NaN は空文字)。"""
    if pd.isna(secs):
        return ""
    minutes = int(secs // 60)
    sec = secs - minutes * 60
    if minutes > 0:
        return f"{minutes}:{sec:05.2f}"
    return f"{sec:.2f}"


def to_nullable_int(s: pd.Series) -> pd.Series:
    """文字列Series → Int64(欠損は <NA>、小数値は四捨五入)。"""
    f = pd.to_numeric(s, errors="coerce")
    return f.round().astype("Int64")


def to_corner_position(s: pd.Series) -> pd.Series:
    """
    通過順位列専用: 1..N の自然数のみ valid とし、0 / 負 / 欠損はすべて <NA>。
    JRA データでは「通過順位が記録されないレース(障害競走の一部など)」で
    0 が入ることがあるため、それを NaN に正規化する。
    """
    n = pd.to_numeric(s, errors="coerce").round()
    return n.where(n >= 1).astype("Int64")


# =====================================================================
# パーサ本体
# =====================================================================

def parse_jra_van_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """
    52列のヘッダー無し JV-Link CSV を pandas で読んだ raw DataFrame
    (header=None / dtype=str を期待) を、アプリ内部スキーマに変換する。

    結果列(着順 / タイム / 上3F)が空でも非空でも、両方で動く。
    空のセルは NaN/<NA>/空文字 として伝搬する。

    返す列: race_id, race_date, racecourse, race_number, race_name,
            post_time, distance, surface, going, finishing_position,
            horse_number, horse_id, horse_name, jockey, trainer,
            weight, carry_weight, weight_change,
            time, last_3f, popularity, odds,
            corner_1, corner_2, corner_3, corner_4
    """
    if raw.shape[1] != JV_LINK_EXPECTED_COLS:
        raise ValueError(
            f"JV-Link 形式は {JV_LINK_EXPECTED_COLS} 列ですが、"
            f"{raw.shape[1]} 列でした。フォーマットを確認してください。"
        )

    # 列インデックスから値を取り出すヘルパ(strip 済み文字列)
    def col(name: str) -> pd.Series:
        return raw[RACES_COL[name]].fillna("").astype(str).str.strip()

    # ----- 日付組み立て(年は 20xx 想定) -----
    yy = col("year").str.zfill(2)
    mm = col("month").str.zfill(2)
    dd = col("day").str.zfill(2)
    race_date = pd.to_datetime("20" + yy + "-" + mm + "-" + dd,
                               format="%Y-%m-%d", errors="coerce")

    # ----- 基本列 -----
    racecourse = col("racecourse")
    race_number = to_nullable_int(col("race_number"))

    # race_id = "R" + yyyymmdd + "-" + 場頭文字 + zfill2(R)
    # 例: R20230722-札01 / R20260503-新01
    race_id = (
        "R"
        + race_date.dt.strftime("%Y%m%d").fillna("00000000")
        + "-"
        + racecourse.str[:1]
        + race_number.astype("string").str.zfill(2)
    )

    # 走破タイム: 秒数(70.3)→ "1:10.30"。空セルは "" のまま
    time_secs = pd.to_numeric(col("time_seconds"), errors="coerce")
    time_str = time_secs.apply(secs_to_time_str)

    # 血統登録番号: 8桁ゼロ埋め(年下2桁 + 6桁通し番号)
    horse_id = col("horse_id").str.zfill(8)

    # 馬番(horse_number)の per-race offset 正規化:
    # JV-Link [24] は概ね 1..N の置換だが、一部のレース(主に古いデータ)では
    # 0..N-1 の 0-based エンコーディングになっている。レース毎の min を引いて
    # +1 することでどちらも 1..N の表現に揃える。
    # 範囲外の値(欠損や明らかな破損)は <NA> にする。
    race_group_key = (
        raw[RACES_COL["year"]].fillna("").astype(str) + "-"
        + raw[RACES_COL["month"]].fillna("").astype(str) + "-"
        + raw[RACES_COL["day"]].fillna("").astype(str) + "-"
        + raw[RACES_COL["racecourse"]].fillna("").astype(str) + "-"
        + raw[RACES_COL["race_number"]].fillna("").astype(str)
    )
    hn_raw = pd.to_numeric(col("horse_number"), errors="coerce")
    # transform を使うとグループ集約値が元と同じ Index・shape で broadcast される
    hn_min_per_race = hn_raw.groupby(race_group_key).transform("min")
    field_size = hn_raw.groupby(race_group_key).transform("size")
    hn_normalized = hn_raw - hn_min_per_race + 1
    # 1..N の範囲に収まらない値は欠損として扱う
    hn_valid = (hn_normalized >= 1) & (hn_normalized <= field_size)
    horse_number = hn_normalized.where(hn_valid).round().astype("Int64")

    # 発走時刻はソース CSV に列が無いため、レース番号から推定して付与する
    post_time = race_number.apply(estimate_post_time)

    return pd.DataFrame({
        "race_id":            race_id,
        "race_date":          race_date.dt.strftime("%Y-%m-%d"),
        "racecourse":         racecourse,
        "race_number":        race_number,
        "race_name":          col("race_name"),
        "post_time":          post_time,
        "distance":           to_nullable_int(col("distance")),
        "surface":            col("surface"),
        "going":              col("going"),
        "finishing_position": to_nullable_int(col("finishing_position")),
        "horse_number":       horse_number,
        "horse_id":           horse_id,
        "horse_name":         col("horse_name"),
        "jockey":             col("jockey"),
        "trainer":            col("trainer"),
        "weight":             to_nullable_int(col("weight")),
        # 斤量(kg、TARGET 形式 column [17])。F3 ルール用。
        # 整数 kg のはずだが念のため float へ正規化(54.5kg 等の半端値対応)。
        "carry_weight":       pd.to_numeric(col("carry_weight"), errors="coerce"),
        # weight_change は元データに無い → 0 固定(prediction_logic は未使用)
        "weight_change":      0,
        "time":               time_str,
        "last_3f":            pd.to_numeric(col("last_3f"), errors="coerce"),
        # popularity も同上 → NaN
        "popularity":         to_nullable_int(col("popularity")),
        "odds":               pd.to_numeric(col("odds"), errors="coerce"),
        # コーナー通過順位(0 や欠損は <NA>。JRA で 0 は記録なし扱い)
        "corner_1":           to_corner_position(col("corner_1")),
        "corner_2":           to_corner_position(col("corner_2")),
        "corner_3":           to_corner_position(col("corner_3")),
        "corner_4":           to_corner_position(col("corner_4")),
    })


# =====================================================================
# 形式・エンコーディング判定(アップロード時に使う)
# =====================================================================

def is_jra_van_headerless(text: str) -> bool:
    """
    1行目の中身を見て、JV-Link 52列ヘッダーなし形式か判定する。

    判定ルール:
    - カンマ区切りで列数が 52 ちょうど
    - 先頭3列(年・月・日)がそれぞれ1〜2桁の数字

    上記を満たさなければ「ヘッダー付き普通CSV」とみなす。
    """
    if not text:
        return False
    first_line = text.split("\n", 1)[0]
    # 末尾 \r を除去
    first_line = first_line.rstrip("\r")
    fields = first_line.split(",")
    if len(fields) != JV_LINK_EXPECTED_COLS:
        return False
    for i in (0, 1, 2):
        v = fields[i].strip().strip('"')
        if not v.isdigit() or len(v) > 2:
            return False
    return True


def is_dc_format(text: str) -> bool:
    """
    TARGET frontier JV の **DC(ダイレクト)系メニュー** から出力された CSV か判定。

    DC 形式の特徴:
    - ヘッダー行なし
    - 全セル数値(コード値・距離・指数など、文字列情報なし)
    - 列数 30〜80 前後(典型 46 列 = 10 ベース + 5 × 7 過去走)
    - 1 列目が 10 桁数字(2桁場 + 2桁年 + 2桁開催 + 2桁R + 2桁馬番)

    判定ルール(全て AND):
    - 列数が JV-Link 52 列形式ではない(明確に区別)
    - 1 行目の 1 列目を strip した文字列が 10 桁数字
    - 1 行目の 1〜10 列目すべてが strip 後に数値として読める
    - 列数が 30 以上 80 未満(極端な誤認回避)

    DC 形式は本アプリで必要な情報(馬名・騎手・上3F・通過順位・馬場 等)を
    含まないため、検出時は早期に専用エラーを出し、お父様に正しいメニュー
    (Z → 開催成績CSV出力 → フルセット+単勝オッズ)への切り替えを案内する。
    """
    if not text:
        return False
    first_line = text.split("\n", 1)[0].rstrip("\r")
    fields = [f.strip().strip('"') for f in first_line.split(",")]
    n_fields = len(fields)
    if n_fields == JV_LINK_EXPECTED_COLS:
        # RA+SE 形式に乗っ取られないよう明示除外
        return False
    if not (30 <= n_fields < 80):
        return False
    # 1 列目: 10 桁数字
    if not (len(fields[0]) == 10 and fields[0].isdigit()):
        return False
    # 先頭 10 列がすべて数値として読めること
    for v in fields[:10]:
        if v == "":
            continue
        # 整数値想定(空白パディング除去後)
        try:
            int(v)
        except ValueError:
            return False
    return True


# DC 形式検出時にユーザーへ表示する日本語エラー文言。
# load_race_card() から ValueError として送出され、app.py の except 節が
# st.error() でそのまま表示する想定。
DC_FORMAT_ERROR_MESSAGE = (
    "この CSV は **TARGET frontier JV の DC(ダイレクト/データカード)系メニュー** からの出力のようです。\n\n"
    "本アプリは『**メインメニュー(Z) → 開催成績CSV出力 → フルセット+単勝オッズ**』からの "
    "エクスポート(52 列形式)を想定しています。\n\n"
    "DC 形式には 馬名 / 騎手 / 斤量 / 上3F / 通過順位 / 馬場 等の情報が含まれないため、"
    "予想ロジックを動かすことができません。\n\n"
    "**お父様への手順案内:**\n"
    "1. TARGET frontier JV を起動\n"
    "2. メインメニュー(Z)を開く\n"
    "3. 「開催成績CSV出力」を選択\n"
    "4. 出力形式で「フルセット+単勝オッズ」を選択\n"
    "5. 当日のレースを範囲指定して CSV 保存\n"
    "6. その CSV を再度本アプリにアップロード\n\n"
    "詳細手順は `docs/DAILY_RACE_CARD.md` をご参照ください。"
)


def decode_with_fallback(raw_bytes: bytes) -> tuple[str, str]:
    """
    バイト列を utf-8-sig → utf-8 → shift_jis → cp932 の順で復号試行。

    成功した最初のエンコーディングで (decoded_text, encoding_name) を返す。
    全部失敗した場合は UnicodeDecodeError を送出。
    """
    encodings = ["utf-8-sig", "utf-8", "shift_jis", "cp932"]
    last_err: UnicodeDecodeError | None = None
    for enc in encodings:
        try:
            return raw_bytes.decode(enc), enc
        except UnicodeDecodeError as e:
            last_err = e
            continue
    # 4種類とも失敗した場合のみここに来る
    assert last_err is not None
    raise UnicodeDecodeError(
        last_err.encoding, last_err.object, last_err.start, last_err.end,
        f"いずれの文字コード({', '.join(encodings)})でも復号できませんでした",
    )
