"""
データ読み込みモジュール。

責務:
1. 過去データ(races / horses / pedigree)を読み込む
   - 本番: data/historical/*.parquet
   - 開発: 上が無ければ data/samples/sample_historical/*.csv にフォールバック
2. お父様がアップロードした当日出馬表 CSV を pandas.DataFrame に変換
3. 当日出馬表の列が想定通りか日本語でバリデーション

Streamlit の app.py からのみ呼ばれる想定。UI には依存しない(疎結合)。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pandas as pd
import streamlit as st

from utils.target_format import (
    DC_FORMAT_ERROR_MESSAGE,
    decode_with_fallback,
    is_dc_format,
    is_jra_van_headerless,
    parse_dc_dataframe,
    parse_jra_van_dataframe,
)


# ファイル名(DCYYMMDD.CSV)から開催日 ISO 文字列を推定する正規表現。
# 例: "DC260509.CSV" → "2026-05-09"。お父様の TARGET 出力命名規則に依存。
import re as _re  # noqa: E402

_DC_FILENAME_RE = _re.compile(r"DC(\d{2})(\d{2})(\d{2})", _re.IGNORECASE)


def _infer_target_date_from_dc_filename(filename: str | None) -> str | None:
    """DC ファイル名から "YYYY-MM-DD" を推定。失敗時は None。"""
    if not filename:
        return None
    m = _DC_FILENAME_RE.search(str(filename))
    if not m:
        return None
    yy, mm, dd = m.group(1), m.group(2), m.group(3)
    return f"20{yy}-{mm}-{dd}"

# ===== 過去データ読み込み =====

# 本番データ(リポジトリ同梱、Parquet形式)の置き場
HISTORICAL_DIR = Path("data/historical")
# 開発時のフォールバック(CSV形式の縮小版)
SAMPLE_HISTORICAL_DIR = Path("data/samples/sample_historical")

# 過去データの3テーブル名
HISTORICAL_TABLES = ("races", "horses", "pedigree")


@dataclass
class HistoricalData:
    """過去データ3テーブルをまとめて持つ簡易コンテナ。"""
    races: pd.DataFrame      # 過去レース結果(行=馬1頭の1出走)
    horses: pd.DataFrame     # 過去登録馬(行=馬1頭)
    pedigree: pd.DataFrame   # 血統情報(行=馬1頭)
    # 各テーブルのデータ出所を独立に持つ。
    # 例: {"races": "parquet", "horses": "csv_sample", "pedigree": "csv_sample"}
    sources: dict[str, str]


def _load_one_table(table_name: str) -> tuple[pd.DataFrame, str]:
    """
    1テーブル分を読み込む。
    本番Parquet (data/historical/{name}.parquet) があればそれを、
    無ければサンプルCSV (data/samples/sample_historical/{name}.csv) を返す。
    どちらも無ければ FileNotFoundError。
    """
    parquet_path = HISTORICAL_DIR / f"{table_name}.parquet"
    csv_path = SAMPLE_HISTORICAL_DIR / f"{table_name}.csv"

    if parquet_path.exists():
        return pd.read_parquet(parquet_path), "parquet"
    if csv_path.exists():
        return pd.read_csv(csv_path), "csv_sample"

    raise FileNotFoundError(
        f"{table_name} のデータが見つかりません。"
        f" {parquet_path} または {csv_path} のいずれかを配置してください。"
    )


def load_historical_data() -> HistoricalData:
    """
    過去データを読み込む。テーブルごとに Parquet / CSV を独立に選択する。
    例えば races のみ本番Parquet、horses/pedigree はサンプルCSV、という混在運用が可能。
    """
    tables: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    for name in HISTORICAL_TABLES:
        df, src = _load_one_table(name)
        tables[name] = df
        sources[name] = src

    return HistoricalData(
        races=tables["races"],
        horses=tables["horses"],
        pedigree=tables["pedigree"],
        sources=sources,
    )


# ===== 当日出馬表(アップロード CSV) =====

# 当日出馬表 CSV に必須の列(これが揃っていないと予想ロジックが回らない)
REQUIRED_RACE_CARD_COLUMNS: tuple[str, ...] = (
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "distance", "surface", "going",
    "horse_id", "horse_name", "jockey", "trainer",
    "weight", "weight_change", "popularity", "odds",
)


def _read_raw_bytes(uploaded_file: IO | str | Path) -> bytes:
    """
    `uploaded_file` を bytes に正規化する。

    Streamlit の `st.file_uploader` 戻り値・通常の file-like・パス文字列・Path
    のいずれにも対応する(後段でエンコーディング自動判定するため、テキストでは
    なく必ず bytes で取り出す)。
    """
    # file-like ならまずシーク(2度目の read に備える)
    if hasattr(uploaded_file, "seek"):
        try:
            uploaded_file.seek(0)
        except Exception:
            # SpooledTemporaryFile 等で失敗しても初回 read なら無視可能
            pass

    if hasattr(uploaded_file, "read"):
        data = uploaded_file.read()
        # まれに既に str になっているケースがあるので bytes に揃える
        return data if isinstance(data, bytes) else data.encode("utf-8")

    return Path(uploaded_file).read_bytes()


def load_race_card(
    uploaded_file: IO | str | Path,
    *,
    filename: str | None = None,
) -> pd.DataFrame:
    """
    アップロードされた当日出馬表 CSV を DataFrame に変換する。

    対応する形式:
    1. **TARGET frontier JV (JRA-VAN) の RA+SE+単勝オッズ 結合 CSV**
       (52列・ヘッダーなし・Shift_JIS が典型)
       → 全フィールド完備、本ロジック v1.1 (rating-based) でフル動作。
       df.attrs["data_format"] = "ra_se"
    2. **TARGET frontier JV の DC(ダイレクト)形式 CSV**(46 列・全数値)
       → 馬名・騎手・上3F 等は欠落するが、TARGET 指数(col[5])と過去 7 走を
         取得して簡易予想画面を成立させる。
       df.attrs["data_format"] = "dc"
       df.attrs["dc_past_runs"] = {horse_id: [run0..run4]}
    3. **ヘッダー付き普通CSV**(英名列の自家製 CSV)
       → そのまま pd.read_csv で読む。data_format は未設定。

    DC 形式時はファイル名 "DCYYMMDD.CSV" から開催日を推定する。

    エンコーディングは utf-8-sig → utf-8 → shift_jis → cp932 の順で試行。
    全部失敗したら ValueError(日本語メッセージ)を送出。
    """
    raw_bytes = _read_raw_bytes(uploaded_file)

    # filename を渡されていない場合は uploaded_file から推定
    if filename is None:
        if isinstance(uploaded_file, (str, Path)):
            filename = Path(str(uploaded_file)).name
        else:
            filename = getattr(uploaded_file, "name", None)

    # 1) エンコーディング自動判定
    try:
        text, _encoding = decode_with_fallback(raw_bytes)
    except UnicodeDecodeError as e:
        raise ValueError(
            "CSVの文字コードを判定できませんでした。"
            "UTF-8 / Shift_JIS / cp932 のいずれかで保存し直してください。"
        ) from e

    # 2a) TARGET DC 形式の検出。馬名・騎手等は欠落するが、TARGET 指数を
    #     使った簡易予想モードでアプリを動作させる。
    if is_dc_format(text):
        raw_df = pd.read_csv(
            io.StringIO(text),
            header=None,
            dtype=str,
            low_memory=False,
        )
        target_date_iso = _infer_target_date_from_dc_filename(filename)
        # parse_dc_dataframe は内部で try/except + None fallback で堅牢化済みだが、
        # 万一の例外もここでキャッチして お父様向けの分かりやすい日本語メッセージで
        # 包む(過去に「Series ambiguous truth value」のような技術的英語例外が
        # 表面化した経緯あり、commit 4db8ce1 後の実 CSV で発覚)。
        try:
            race_card_df, past_runs_by_horse = parse_dc_dataframe(
                raw_df, target_date_iso=target_date_iso,
            )
        except Exception as e:
            raise ValueError(
                f"DC 形式の CSV を読み込みましたが、内部解析でエラーが発生しました。\n"
                f"ファイル名: {filename or '(不明)'}\n"
                f"内部エラー: {type(e).__name__}: {e}\n\n"
                f"対処方法: お手数ですが、TARGET frontier JV からの再エクスポートを"
                f"お試しください(同じ DC メニューで OK)。"
            ) from e
        race_card_df.attrs["data_format"] = "dc"
        race_card_df.attrs["dc_past_runs"] = past_runs_by_horse
        race_card_df.attrs["source_filename"] = filename or ""
        return race_card_df

    # 2b) TARGET 52列ヘッダーなし(RA+SE+単勝オッズ)形式
    if is_jra_van_headerless(text):
        raw_df = pd.read_csv(
            io.StringIO(text),
            header=None,
            dtype=str,
            low_memory=False,
        )
        df = parse_jra_van_dataframe(raw_df)
        df.attrs["data_format"] = "ra_se"
        df.attrs["source_filename"] = filename or ""
        return df

    # 3) ヘッダー付き普通CSV(既存サンプルや日本語列名 CSV はこのパス)
    df = pd.read_csv(io.StringIO(text))
    df.attrs["data_format"] = "header_csv"
    df.attrs["source_filename"] = filename or ""
    return df


# =====================================================================
# DC 形式 → historical 連携(v1.2 フルモード化)
# =====================================================================
# DC 形式は馬名・騎手・上3F・通過順位を持たないため、過去走パターンマッチで
# historical/races.parquet 側の horse_id を特定し、欠落フィールドを引き当てる。
# マッチ失敗馬は元の DC 簡易モード(「馬番 N」表示・TARGET 指数のみ)で運用。

def enrich_dc_with_historical(
    race_card_df: pd.DataFrame,
    historical_df: pd.DataFrame,
    *,
    today_going: str = "良",
) -> pd.DataFrame:
    """
    DC 形式の race_card_df を historical/races.parquet と照合して、
    マッチ成功馬には 馬名 / 騎手 / 過去走の上3F・通過順位・馬場 を補完する。

    引数:
        race_card_df: load_race_card 戻り値。attrs["data_format"]="dc" 想定。
        historical_df: data/historical/races.parquet を読んだ DataFrame。
        today_going: 当日馬場(良 / 稍重 / 重 / 不良)。UI ラジオの値。
                     race_card_df["going"] と past_runs に設定される
                     (DC は当日馬場を持たないため UI から外部入力)。

    戻り値:
        enriched race_card_df(コピー、入力は変更しない)
        attrs:
          dc_past_runs   ← 過去走 dict も historical 値で再構築
          dc_match_count ← マッチ成功頭数
          dc_total_count ← 全頭数
          dc_going        ← today_going
    """
    from utils.horse_matcher import match_all_dc_horses  # 遅延 import で循環回避

    if race_card_df.attrs.get("data_format") != "dc":
        return race_card_df

    df = race_card_df.copy()
    # attrs はコピーで失われるので明示再設定
    df.attrs.update(race_card_df.attrs)

    target_date = str(df["race_date"].iloc[0]) if not df.empty else ""
    dc_past_runs = dict(df.attrs.get("dc_past_runs", {}))

    # 1) 全馬のマッチング
    dc_horse_ids = df["horse_id"].astype(str).tolist()
    matches = match_all_dc_horses(
        dc_horse_ids, dc_past_runs, historical_df, target_date,
    )

    # 2) historical を horse_id でひける高速 lookup
    matched_ids = [m.matched_horse_id for m in matches.values() if m.matched_horse_id]
    if matched_ids:
        hist_subset = historical_df[
            historical_df["horse_id"].astype(str).isin(matched_ids)
        ].copy()
    else:
        hist_subset = historical_df.iloc[0:0].copy()

    # 各馬の最新走情報(馬名 取得用)
    if not hist_subset.empty:
        hist_subset_sorted = hist_subset.sort_values("race_date", ascending=False)
        latest_per_horse = hist_subset_sorted.drop_duplicates("horse_id", keep="first")
        latest_per_horse = latest_per_horse.set_index("horse_id")
    else:
        hist_subset_sorted = hist_subset
        latest_per_horse = pd.DataFrame()

    # 騎手名は最新走で空文字 / NaN だったら過去走を遡って有効値を探す。
    # historical のデータ品質で「最新走の jockey」だけ抜けてるケースがあるため。
    # **perf**: 旧実装は呼び出しごとに全 hist_subset_sorted を再フィルタ +
    # iterrows していた。jockey-by-horse_id の有効値マップを 1 度だけ前処理する。
    valid_jockey_by_hid: dict[str, str] = {}
    if not hist_subset.empty:
        # まず最新走で有効値があるものを採用
        for hid, jockey in zip(
            latest_per_horse.index.astype(str),
            latest_per_horse.get("jockey", pd.Series(dtype=str)).fillna(""),
        ):
            j = str(jockey).strip()
            if j and j.lower() != "nan":
                valid_jockey_by_hid[hid] = j
        # 最新走で取れなかった馬だけ過去走を遡って探す(数件想定で軽量)
        missing = set(latest_per_horse.index.astype(str)) - set(valid_jockey_by_hid)
        if missing:
            sorted_records = hist_subset_sorted[
                hist_subset_sorted["horse_id"].astype(str).isin(missing)
            ][["horse_id", "jockey"]].to_dict("records")
            for rec in sorted_records:
                hid = str(rec["horse_id"])
                if hid in valid_jockey_by_hid:
                    continue
                j = str(rec.get("jockey") or "").strip()
                if j and j.lower() != "nan":
                    valid_jockey_by_hid[hid] = j

    def _latest_valid_jockey(hist_hid: str) -> str:
        return valid_jockey_by_hid.get(str(hist_hid), "")

    # 各馬の過去 10 走(target_date より前)を historical から取得し、
    # **dict のリストに変換** しておく。後段の各馬ループで iterrows しないで済む。
    # v1.4: ルール評価対象を 5 走 → 10 走に拡張(ベテラン馬の長期実績を拾う)
    if not hist_subset.empty:
        past = hist_subset[hist_subset["race_date"] < target_date].copy()
        past = past.sort_values("race_date", ascending=False)
        # 馬 ID → 直近 10 走の dict リスト(numpy/pandas の row オブジェクトを
        # 介さず純 Python dict にしておけば、後段ループは O(1) 参照)
        past_grouped: dict[str, list[dict]] = {}
        # head(10) を groupby+head で一気にやって to_dict で抜き出す
        head10_df = past.groupby("horse_id", sort=False).head(10)
        # to_dict("records") は内部 C ループで iterrows より 50-100 倍速い
        records = head10_df[[
            "horse_id", "race_date", "racecourse", "surface", "distance",
            "going", "finishing_position", "last_3f", "jockey", "carry_weight",
            "corner_1", "corner_2", "corner_3", "corner_4",
        ]].to_dict("records")
        for rec in records:
            hid = str(rec["horse_id"])
            past_grouped.setdefault(hid, []).append(rec)
    else:
        past_grouped: dict[str, list[dict]] = {}

    # 3) df の各行を更新(マッチ成功馬のみ)
    new_horse_names: list[str] = []
    new_jockeys: list[str] = []
    matched_hist_ids: list[str | None] = []
    confidences: list[str] = []        # "high" / "medium" / "none"
    past_run_counts: list[int] = []    # 元 DC の有効過去走数(失敗馬の表示分岐用)
    new_past_runs_by_horse: dict[str, list[dict | None]] = {}

    def _count_valid_dc_runs(runs: list[dict | None]) -> int:
        """DC 元データの有効過去走数(distance > 0 のもの)。"""
        return sum(
            1 for r in runs
            if isinstance(r, dict) and (r.get("distance") or 0) > 0
        )

    # **perf**: 旧実装は df.iterrows() で 495 行を Python ループ + 内部で
    # past_for_horse.iterrows() を再度 → 約 8 秒の hot path。
    # to_dict("records") で plain dict のリストに変換 + past_grouped を
    # 既に dict リスト化済み(上で前処理)にしてあるため、内側ループも
    # 純 Python dict 操作で済む。
    df_records = df[
        ["horse_id", "horse_number", "horse_name"]
    ].to_dict("records")
    # latest_per_horse は MultiIndex 不可、horse_name のみ事前抽出
    latest_horse_name_by_hid: dict[str, str] = {}
    if not latest_per_horse.empty and "horse_name" in latest_per_horse.columns:
        latest_horse_name_by_hid = (
            latest_per_horse["horse_name"].astype(str).to_dict()
        )

    def _to_run_dict(rec: dict) -> dict:
        """historical の records 1 行を rating engine 入力形式の dict に変換。"""
        def _i(k):
            v = rec.get(k)
            return int(v) if pd.notna(v) else None
        def _f(k):
            v = rec.get(k)
            return float(v) if pd.notna(v) else None
        dist = rec.get("distance")
        return {
            "race_date":          str(rec.get("race_date") or ""),
            "racecourse":         str(rec.get("racecourse") or ""),
            "surface":            str(rec.get("surface") or ""),
            "distance":           int(dist) if pd.notna(dist) else 0,
            "going":              str(rec.get("going") or ""),
            "finishing_position": _i("finishing_position"),
            "last_3f":            _f("last_3f"),
            "jockey":             str(rec.get("jockey") or "").strip() or "(不明)",
            "carry_weight":       _f("carry_weight"),
            "corner_1":           _i("corner_1"),
            "corner_2":           _i("corner_2"),
            "corner_3":           _i("corner_3"),
            "corner_4":           _i("corner_4"),
        }

    for row in df_records:
        dc_hid = str(row["horse_id"])
        result = matches.get(dc_hid)
        confidence = result.confidence if result else "none"
        n_dc_runs = _count_valid_dc_runs(dc_past_runs.get(dc_hid, []))
        past_run_counts.append(n_dc_runs)
        confidences.append(confidence)
        if (result and result.matched_horse_id
                and result.matched_horse_id in latest_horse_name_by_hid):
            hist_hid = result.matched_horse_id
            new_horse_names.append(
                latest_horse_name_by_hid.get(hist_hid) or row["horse_name"]
            )
            # v1.7.3: 「(当日確認)」プレースホルダを廃止。jockey 不明時は「—」
            valid_jockey = _latest_valid_jockey(hist_hid)
            new_jockeys.append(valid_jockey or "—")
            matched_hist_ids.append(hist_hid)
            # past_grouped は既に dict リスト化済み(上で前処理)
            past_records = past_grouped.get(hist_hid, [])
            runs10: list[dict | None] = [_to_run_dict(r) for r in past_records]
            while len(runs10) < 10:
                runs10.append(None)
            new_past_runs_by_horse[dc_hid] = runs10
        else:
            # v1.7.3: マッチ失敗時のラベルサフィックスを撲滅。
            # 旧: 「馬番6(過去走少)」「馬番N(DB照合不能)」「馬番N(新馬)」
            # 新: 「馬番N」のみ(過去走 0 走の真の新馬は識別子として末尾★)
            # → スクショで報告された「馬番6(過去走少)((当日確認))」のような
            #   重複プレースホルダ表示を完全に防ぐ。マッチ成功すれば実名、
            #   失敗すれば馬番だけのシンプル表示で UI を統一。
            try:
                hno = int(row["horse_number"])
            except (ValueError, TypeError):
                hno = 0
            if n_dc_runs == 0:
                # 真の新馬のみ末尾に小さなマーカー(過去走 0 確実、識別用)
                new_horse_names.append(f"馬番{hno} 🆕")
            else:
                new_horse_names.append(f"馬番{hno}")
            new_jockeys.append("—")  # 「(当日確認)」廃止
            matched_hist_ids.append(None)
            failed_runs = list(dc_past_runs.get(dc_hid, []))
            while len(failed_runs) < 10:
                failed_runs.append(None)
            new_past_runs_by_horse[dc_hid] = failed_runs[:10]

    df["horse_name"] = new_horse_names
    df["jockey"] = new_jockeys
    df["matched_historical_horse_id"] = matched_hist_ids
    df["match_confidence"] = confidences
    df["dc_past_run_count"] = past_run_counts
    # 当日馬場を全行に設定(rating engine が going を読む)
    df["going"] = today_going

    # attrs を更新(信頼度別カウントも保持)
    df.attrs["dc_past_runs"] = new_past_runs_by_horse
    df.attrs["dc_match_count"] = sum(1 for h in matched_hist_ids if h)
    df.attrs["dc_match_count_high"] = sum(1 for c in confidences if c == "high")
    df.attrs["dc_match_count_medium"] = sum(1 for c in confidences if c == "medium")
    df.attrs["dc_total_count"] = len(matched_hist_ids)
    df.attrs["dc_going"] = today_going
    return df


# =====================================================================
# enrich スキーマバージョン(v1.7.4 で導入)
# =====================================================================
# v1.7.3 で「(過去走少)」「(当日確認)」プレースホルダ廃止 + 騎手「—」化を
# 行ったが、`enrich_dc_with_historical_cached` の cache key には file_hash
# と today_going しか含まれず、Streamlit Cloud / ローカルともに **既存の
# キャッシュが古いプレースホルダ入り DataFrame を return し続けて** 修正が
# 反映されない問題が発生した。
#
# このバージョン文字列を cache_key に追加することで、enrich の出力スキーマ
# を変える時に **自動的に古いキャッシュを無効化** する。以降ラベル表記や
# 列構成を変える時はこの文字列を bump する運用にする。
ENRICH_SCHEMA_VERSION = "v4-style-multi-tier"


@st.cache_data(show_spinner="DC 形式の過去走を historical と照合中…")
def enrich_dc_with_historical_cached(
    schema_version: str,
    file_hash: str,
    today_going: str,
    _race_card_df: pd.DataFrame,
    _historical_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    enrich_dc_with_historical のキャッシュ版。

    キャッシュキー = (schema_version, file_hash, today_going)。
    schema_version が変わると古いキャッシュが自動的に無効化されるため、
    プレースホルダ廃止や列追加などスキーマ変更時に確実に新コードが走る。

    file_hash が同じ + 同じ going + 同じ schema_version なら 159k 行
    スキャン ×N 馬 を再実行しない。DataFrame は _ プレフィックスで
    Streamlit のハッシュ対象から除外。pandas attrs はキャッシュ pickle で
    保持されるので戻り値のみで OK。
    """
    # schema_version は cache key として使うのみ、実際の処理では参照しない
    del schema_version
    return enrich_dc_with_historical(
        _race_card_df, _historical_df, today_going=today_going,
    )


@st.cache_data(show_spinner="出馬表を読み込み中…")
def load_race_card_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    load_race_card のキャッシュ版。同じバイト列の再読み込みを回避する。

    キャッシュキーは (file_bytes, file_name) のタプル。Streamlit はバイト列の
    内容ハッシュを取るので、ファイル内容が同じなら名前が違っても同じキャッシュ
    エントリにヒットする(file_name は表示用に残しているだけ)。

    Streamlit の st.file_uploader が返す UploadedFile は内部バッファを巻き戻し
    再利用するたびに seek が必要なため、Streamlit 側ですでに getvalue() してから
    呼び出すことを期待している。
    """
    return load_race_card(io.BytesIO(file_bytes), filename=file_name)


@dataclass
class ValidationResult:
    """出馬表バリデーション結果。UI 側でメッセージ表示するため日本語で詰める。"""
    ok: bool
    missing_columns: list[str]    # 不足している列名
    extra_columns: list[str]      # 想定外の余分な列(参考情報、エラーにはしない)
    message: str                  # 画面表示用の日本語メッセージ


# DC 形式で最低限揃っていれば OK とする緩い必須列セット
DC_REQUIRED_COLUMNS: tuple[str, ...] = (
    "race_id", "race_date", "racecourse", "race_number",
    "horse_id", "horse_number", "horse_name",
    "distance", "surface", "target_index",
)


def validate_race_card(df: pd.DataFrame) -> ValidationResult:
    """
    出馬表の列構成をチェック。
    DC 形式(df.attrs["data_format"] == "dc")の場合は緩い検証(最低限の列だけ)。
    それ以外は従来通り REQUIRED_RACE_CARD_COLUMNS を全て要求する。
    """
    actual = set(df.columns)
    data_format = df.attrs.get("data_format", "")

    if data_format == "dc":
        expected = set(DC_REQUIRED_COLUMNS)
        missing = sorted(expected - actual)
        if missing:
            msg = (
                "DC 形式として読み込みましたが、必要な列が不足しています。\n"
                f"不足している列: {', '.join(missing)}"
            )
            return ValidationResult(
                ok=False, missing_columns=missing, extra_columns=[], message=msg,
            )
        return ValidationResult(
            ok=True, missing_columns=[], extra_columns=[],
            message="DC 形式で読み込み(簡易予想モード)",
        )

    # RA+SE / ヘッダー付き CSV: 従来の厳しい検証
    expected = set(REQUIRED_RACE_CARD_COLUMNS)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    if missing:
        msg = (
            "列名が想定と異なります。\n"
            f"不足している列: {', '.join(missing)}\n"
            f"想定される列(順不同): {', '.join(REQUIRED_RACE_CARD_COLUMNS)}"
        )
        return ValidationResult(ok=False, missing_columns=missing, extra_columns=extra, message=msg)

    msg = "列構成OK。" + (f"想定外の追加列: {', '.join(extra)}" if extra else "")
    return ValidationResult(ok=True, missing_columns=[], extra_columns=extra, message=msg)


# ===== 集計ヘルパ(UI でファイルプレビュー表示するため) =====

def summarize_race_card(df: pd.DataFrame) -> dict[str, int]:
    """
    出馬表の概要(レース数・出走馬数)を返す。UI のメトリクス表示用。
    """
    return {
        "race_count": int(df["race_id"].nunique()) if "race_id" in df.columns else 0,
        "horse_count": int(len(df)),
    }
