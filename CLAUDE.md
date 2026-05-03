# 競馬予想アプリ (keiba-yosou)

## 案件概要
JRA中央競馬の予想を自動化する Streamlit Webアプリ。
個人利用(開発者の父親が日常使い)。商用配布は一切しない。
お父様の予想にかかる時間を、現状1〜2時間 → 5分に短縮することが目的。

## ターゲットユーザー
- 開発者の父親(Windows 10/11、ブラウザは Edge)
- JRA-VAN DataLab 契約者
- 競馬歴が長く、独自の予想ロジックを言語化できる
- PC操作は中級レベル(ブラウザ操作・ファイル管理は問題なし)

## 配布形態
Streamlit Community Cloud (無料プラン・公開リポジトリ)
- お父様はブラウザでURLアクセス → 当日出馬表CSVアップロード → 予想結果表示
- お父様PCには何もインストール不要

## 技術スタック
- 言語: Python 3.11+
- フレームワーク: Streamlit
- データ処理: pandas, numpy, pyarrow (Parquet用)
- 配布: Streamlit Cloud + GitHub 自動連携
- 過去データ形式: Parquet(GitHub リポジトリに同梱)
- 当日データ形式: CSV(お父様がアップロード)

## なぜこの構成か
- Streamlit Cloud: 無料、お父様PC設定不要、URLアクセスのみで利用可能
- Parquet: 1年分の過去データを CSV ではなく Parquet で持つことで GitHub の100MB制限内に収まる(120MB → 12MB)
- リポジトリ同梱: Streamlit Cloud はセッション間でアップロードファイルを保持しないため、固定データはリポジトリに含める

## データ構造

### 過去データ(リポジトリ同梱、月1回更新)
data/historical/races.parquet:
  race_id, race_date, racecourse, race_number, race_name,
  distance, surface, going, finishing_position, horse_id,
  horse_name, jockey, trainer, weight, weight_change,
  time, last_3f, popularity, odds

data/historical/horses.parquet:
  horse_id, horse_name, sex, age, sire, dam, dam_sire,
  total_starts, wins, places, shows

data/historical/pedigree.parquet:
  horse_id, sire_line, broodmare_sire_line, inbreeding_score

### 当日データ(お父様がアップロード、CSV形式)
- 当日出馬表(JV-Link または TARGET frontier JV のエクスポート形式)
- 列構造は過去データの races と概ね同一

## MVP機能(初回リリース)
1. 当日出馬表CSVのドラッグ&ドロップアップロード
2. アップロードファイルのプレビュー(レース数、出走馬数表示)
3. 「予想実行」ボタン
4. レースごとの推奨馬リスト表示(◎○▲△ + スコア)
5. 推奨理由の表示(クリックで展開)
6. 予想結果の CSV/PDF ダウンロード

## 推奨馬選定ロジック(MVP段階のダミー、後から差し替え可能な設計に)
お父様の本ロジックは別途ヒアリング後に実装。
MVP段階では以下の暫定ロジックで動作確認:
- 直近3走の平均着順 × -10点
- 直近3走の上がり3F平均が33.5秒未満なら +20点
- 騎手の年間勝率 × 100点
- 距離適正(同距離での連対率) × 50点
合計スコア順に各レース上位4頭を推奨(◎○▲△)。

このロジックは prediction_logic.py に分離し、
将来お父様の本ロジックに差し替え可能な構造で実装すること。
データ読み込み・UI 部分とは疎結合にする。

## 将来の拡張機能
- お父様の本ロジック実装(ヒアリング後)
- 予想結果の的中履歴管理
- 馬券種別の最適買い目提案
- 過去データ更新の半自動化(GitHub Actions)
- 予想ルールのYAML設定化(コード書かずに調整可能に)

## 開発ルール
- 機能追加・修正ごとに feature/xxx ブランチを切る
- main は常に Streamlit Cloud で動作する状態を維持
- requirements.txt は `pip freeze > requirements.txt` で更新
- 過去データは大きいので samples/ に縮小版(1ヶ月分)も置いておく

## ディレクトリ構造
keiba-yosou/
├── app.py                          # Streamlit エントリーポイント
├── prediction_logic.py             # 予想ロジック(差し替え可能設計)
├── data_loader.py                  # データ読み込み
├── requirements.txt
├── README.md
├── CLAUDE.md
├── .gitignore
├── .streamlit/
│   └── config.toml                 # Streamlit設定
├── data/
│   ├── historical/                 # 過去データ(Parquet、リポジトリ同梱)
│   │   ├── races.parquet
│   │   ├── horses.parquet
│   │   └── pedigree.parquet
│   └── samples/                    # 開発用ダミーデータ(CSV、軽量)
│       ├── sample_race_card.csv    # 当日出馬表サンプル
│       └── sample_historical/      # 過去データサンプル(1週間分)
└── tests/
    └── test_prediction.py          # ロジックの単体テスト

## 制約・要件
- お父様はブラウザのみで完結すること(インストール作業ゼロ)
- 公開リポジトリにする予定なので機密情報含めない
- 1年分の過去データを高速に処理(初回起動5秒以内)
- 当日データのアップロードから予想結果表示まで10秒以内

## エラーハンドリング方針
- 想定外のCSV列構成: 日本語で「列名が想定と異なります」と表示し期待する列名一覧を提示
- データ不足の馬: 「過去データなし」と表示してスキップ
- ファイル読み込み失敗: 日本語エラーメッセージ
- 予想ロジック内例外: 該当馬をスキップして処理継続

## やってはいけないこと
- お父様の馬券購入履歴・予算情報をコードに含めない
- 第三者への予想結果配信機能を作らない
- 「的中保証」のような表現を UI に入れない
- 予想を有料コンテンツ化する仕組みを入れない(個人利用専用)
