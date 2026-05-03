# 過去データ差し替え手順

お父様(または JRA-VAN DataLab / TARGET frontier JV のエクスポート)から受領した
最新の過去データを、Streamlit Cloud で配信する Parquet ファイルに変換して反映する手順。

## 想定運用頻度
**月1回**(月初に前月分を追加するイメージ)。

---

## 前提

- 受領する CSV は **3種類**(MVP で扱っているテーブル):
  - `races_*.csv` … 過去レース結果(行=馬1頭の1出走)
  - `horses_*.csv` … 馬マスタ
  - `pedigree_*.csv` … 血統情報
- 列構成は `data/samples/sample_historical/*.csv` と同じである必要がある
  - 違う場合は `scripts/csv_to_parquet.py` の列リネーム表に追記する

## 手順

### a. お父様の CSV を `data/raw/` に置く

```text
data/
└── raw/                          # ← .gitignore 対象。生CSVはコミットしない
    ├── races_202604.csv
    ├── horses_202604.csv
    └── pedigree_202604.csv
```

ファイル名は `races_*.csv` / `horses_*.csv` / `pedigree_*.csv` のパターンに合わせること
(複数月分まとめて置けば結合される)。

### b. 変換スクリプトを実行

```bash
source .venv/bin/activate
python scripts/csv_to_parquet.py
```

スクリプトは以下を行う:
1. `data/raw/{races,horses,pedigree}_*.csv` をすべて読み込んでテーブル毎に結合
2. 列名・型を `data/historical/` 想定スキーマに正規化
   - 想定外の列は警告を出す(失敗はしない)
   - 必須列が無ければエラーで停止
3. 重複行を除外(`races` は race_id+horse_id、`horses`/`pedigree` は horse_id)
4. `data/historical/{races,horses,pedigree}.parquet` に書き出し
5. 行数・サイズ・想定外列を最後にレポート

### c. 生成された Parquet を確認

```bash
ls -lh data/historical/
```

- `races.parquet`: 1年分で 5〜15MB 程度を想定
- `horses.parquet`: 数十〜数百KB
- `pedigree.parquet`: 数十KB〜数MB

> ⚠️ **GitHub の単一ファイル上限は 100MB**。それを超える場合は
> 期間を絞るか Git LFS の導入を検討する。

### d. コミット & push

`.gitignore` で `data/historical/*.parquet` を除外しているため `-f` が必要:

```bash
git checkout -b chore/update-historical-data-202604
git add -f data/historical/*.parquet
git commit -m "chore: update historical data through 2026-04"
git push -u origin chore/update-historical-data-202604
```

GitHub で PR を作成 → main にマージ。

### e. Streamlit Cloud が自動再デプロイ

- main への merge 後、Streamlit Cloud が **数分以内に自動再デプロイ**
- お父様には `https://<your-app-name>.streamlit.app/` をリロードしてもらえばOK
- サイドバーの「データ種別」が **「本番(Parquet)」** になっていれば差し替え成功

---

## トラブルシューティング

### 「列名が想定と異なります」と表示される
受領 CSV の列名が変わっている可能性。以下のいずれか:
1. CSV の列名を手動で正規化してから `data/raw/` に置く
2. `scripts/csv_to_parquet.py` の `RENAME_*` 辞書に対応エントリを追加

### Parquet が大きすぎて push できない
- `races.parquet` を期間で分割する
- 古い年度を別リポジトリに退避
- Git LFS を導入(無料枠 1GB)

### Streamlit Cloud が再デプロイしない
- Streamlit Cloud の管理画面 ([share.streamlit.io](https://share.streamlit.io)) で
  対象アプリの「Reboot app」を実行
- Python バージョン・requirements.txt の差分を確認
