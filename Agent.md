# Agent.md — Autonomous Fraud Investigation Agent 仕様（PoC）

本書は `credit_detect` の日次 BQML パイプラインの上に載せる自律調査エージェントの **実装仕様** です。ビジネス背景とコスト方針の原本は設計メモ（Agent基本設計）です。本ドキュメントは、その設計を **実際のテーブル名・モデル名・制約** に落とし込んだものです。

- 対象ブランチ: `agent/poc`
- 実装パッケージ: `agent/`
- GCP 構築手順: [README.md](README.md) の「Autonomous Fraud Investigation Agent の GCP 構築」

---

## 1. 目的と範囲

### 1.1 目的

不正対策アナリストが自然言語で指示したとき、エージェントが次を **人手を介さず完遂** する。

1. 指示を調査タスクに分解する（Planning）
2. 社内規程・SOP・インシデントナレッジをベクトル検索する（Hybrid RAG）
3. `dwh_prod` の推論結果に対して Text-to-SQL を生成・実行する
4. PCA 特徴量（V1〜V28）を SOP-MLOPS-2026-002 のカーネルで統計評価する
5. 結果の十分性を自己採点し、不足なら SQL を組み直す（Reflection）
6. 監査向け Markdown レポートと、監視用 SELECT（是正 SQL の提案）を返す

### 1.2 PoC でやること / やらないこと

| やる | やらない（意図的） |
| --- | --- |
| 読み取り SQL の生成・実行 | BigQuery への DELETE/UPDATE/CREATE の実行 |
| 規程ドキュメントのチャンク化・埋め込み・検索 | Vertex AI Vector Search（Index Endpoint 常時課金） |
| SOP 第7章相当の KS / IV / Cliff's Delta | LLM 生成 Python の任意 `exec` |
| Cloud Run 上での LangGraph 実行 | 本番自動遮断の適用 |
| Vertex AI Agent Engine へのデプロイ手順 | 多テナント認証・IAP までの本番hardening |

是正 SQL は **提案** までです。適用は Dataform のレビュー付き変更を人間が行います。

### 1.3 既存パイプラインとの関係

```
[credit_detect 日次バッチ]
  Cloud Scheduler → Cloud Run Jobs → Workflows → Dataform → BQML
       ↓
  dwh_prod.ulb_fraud_detection_predictions
  dwh_prod.ulb_fraud_detection_model
  dwh_prod.ulb_fraud_detection_*（評価テーブル群）
       ↓
[本 Agent]
  自然言語 → LangGraph → 上記テーブルの読取 + 規程 RAG → レポート
```

設計メモのプレースホルダ `credit_detect.daily_prediction` は、本リポジトリでは使いません。対応は次のとおりです。

| 設計メモ | 本実装（Dataform 実績） |
| --- | --- |
| `credit_detect.daily_prediction` | `{project}.dwh_prod.ulb_fraud_detection_predictions` |
| `predicted_Class_probs` 配列 | スカラー列 `fraud_probability`（label=1 の prob を抽出済み） |
| BQML モデル名未指定 | `{project}.dwh_prod.ulb_fraud_detection_model` |
| ナレッジデータセット `credit_detect` | 同一データセット `dwh_prod` |

---

## 2. 論理アーキテクチャ

```
[アナリスト]
    │ 自然言語 + 任意のマニュアルアップロード
    ▼
[Cloud Run: Streamlit UI]
    │
    ├─ Path A（PoC 推奨）: 同一コンテナ内で LangGraph を invoke
    └─ Path B（設計準拠）: Vertex AI Agent Engine（旧 Reasoning Engine）の query()
            │
            ▼
[FraudInvestigationAgent]
    Planner → Hybrid RAG → SQL Gen → SQL Exec
                    ▲              │
                    │         エラーかつ sql_retry < 2
                    │              ▼
                    │         Python 統計カーネル
                    │              ▼
                    └──不足── Reflection ──十分／上限──→ Output
```

### 2.1 ランタイムモード

環境変数 `AGENT_BACKEND`（UI サイドバーでも切替可）

| 値 | 動作 |
| --- | --- |
| `mock` | Gemini / BigQuery を呼ばない。スクリプト LLM + 固定行 + ローカル規程検索。CI と画面デモ用。 |
| `local` | プロセス内で `ChatVertexAI` + BigQuery。Cloud Run の既定。 |
| `reasoning_engine` | デプロイ済み Agent Engine リソースの `query(user_query=...)` を呼ぶ。 |

### 2.2 コンポーネントとソース

| コンポーネント | パス | 責務 |
| --- | --- | --- |
| ラッパ | `agent/agent.py` | `set_up()` / `query()`（Agent Engine 契約） |
| 状態 | `agent/state.py` | `AgentState` |
| グラフ | `agent/graph.py` | LangGraph ノードと条件分岐 |
| プロンプト | `agent/prompts.py` | 各ノードの指示文。スキーマを注入 |
| SQL ガード | `agent/sql_guard.py` | SELECT/WITH 以外を拒否、LIMIT 付与 |
| BQ 実行 | `agent/tools/bq_client.py` | `maximum_bytes_billed` 付き query |
| RAG | `agent/tools/bq_vector_search.py` | 埋め込み + `VECTOR_SEARCH` |
| 統計 | `agent/tools/data_analyzer.py` | `evaluate/evaluate_feature.py` を再利用 |
| 投入 | `agent/tools/knowledge_ingest.py` | アップロード文書のチャンク化 |
| UI | `app/app.py` | Streamlit |
| IaC | `terraform/agent.tf` | API / SA / GCS / ナレッジテーブル / 任意の Cloud Run |

---

## 3. 状態スキーマ（`AgentState`）

設計メモのフィールドを実装し、SQL リトライが Reflection 回数に依存しないよう **`sql_retry_count` と `reflection_score` を追加** しています。

| フィールド | 型 | 意味 |
| --- | --- | --- |
| `user_query` | str | ユーザーの元指示 |
| `plan` | list[str] | Planner が分解した手順 |
| `current_step` | int | 予約（将来のステップ実行用） |
| `retrieved_policies` | list[dict] | RAG ヒット（doc_id, category, title, content, distance） |
| `generated_sql` | str \| None | 直近の調査 SQL |
| `sql_execution_result` | list[dict] \| None | BigQuery 行（最大 200） |
| `sql_error` | str \| None | ガード違反または BQ エラー |
| `sql_retry_count` | int | SQL 失敗回数。2 未満なら SQL Gen へ戻す |
| `analysis_code` | str \| None | 実行したカーネル名（任意コードは持たない） |
| `statistical_summary` | dict \| None | KS / IV / Cliff / 件数など |
| `critique` | str | Reflection のコメント |
| `reflection_score` | int | 0–100。80 未満は不足 |
| `is_sufficient` | bool | 終了判定 |
| `retry_count` | int | Reflection 実行回数 |
| `final_report` | str | Markdown 監査レポート |
| `remediation_sql` | str | 監視用 SELECT の提案 |

`query()` の戻り値は UI / Agent Engine クライアントが使うサブセットです（計画、生成 SQL、統計、レポート、是正 SQL、ヒットした規程の抜粋）。

---

## 4. ノード仕様

最大ループ: SQL 再生成 2 回、Reflection 再調査 2 回。それ以上は得られた証拠のまま Output に進み、レポートに限界を書きます。

### 4.1 Planner (`plan_step`)

- 入力: `user_query` + 実スキーマカタログ（`agent/config.py` の `schema_prompt`）
- 出力: 番号付き手順リスト
- 禁止: 存在しないテーブル名の発明。`daily_prediction` や `predicted_Class_probs` を使わせない（プロンプトで明示）

### 4.2 Hybrid RAG (`rag_step`)

- クエリ埋め込み: Vertex AI `text-embedding-004`（768 次元）
- 検索: BigQuery `VECTOR_SEARCH`（COSINE）。失敗時は `LIKE` キーワード検索にフォールバック
- 初期コーパス: `knowledge/*.md`（SOP 要約、POL-SEC-2026-004、カードテスト、エスカレーション）
- **アップロード:** UI または `scripts/seed_knowledge.py --file` が文書をチャンク（見出し優先、約 900 文字、120 文字オーバーラップ）し、同じテーブルへ UPSERT する
- `doc_id` は title+uri のハッシュ + チャンク番号。再投入は同一 `doc_id` を DELETE してから INSERT（冪等）

カテゴリ:

| category | 例 |
| --- | --- |
| `POLICY` | SOP、社内調査基準 |
| `PCI_DSS` | PCI 関連の調査観点（規格本文の複製ではない） |
| `INCIDENT_CASE` | カードテスト / BIN アタック |
| `UPLOADED` | アナリストが後から上げた資料 |

### 4.3 SQL Gen (`sql_step`)

- 方言: BigQuery 標準 SQL
- 既定の高リスク条件: `fraud_probability >= 0.85`（POL-SEC-2026-004 第2条）
- 「昨日」は擬似日付 `Date` の `MAX(Date)` と解釈するようスキーマ注記で指示
- 前回 `sql_error` または Reflection の `critique` をプロンプトに再注入して修正する

### 4.4 SQL Exec (`execute_sql_step`)

実行前に `sanitize_sql` を通す。

- 許可: 先頭が `SELECT` または `WITH`
- 拒否: INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/ALTER/TRUNCATE/GRANT/EXPORT/CALL 等
- 複数ステートメント（`;`）を除去
- 末尾 `LIMIT` が無ければ `LIMIT 200` を付与
- `QueryJobConfig.maximum_bytes_billed = 10GiB`

失敗かつ `sql_retry_count < 2` → SQL Gen。成功または上限 → Analyzer。

### 4.5 Analyzer (`analysis_step`)

LLM コードは実行しません。`evaluate/evaluate_feature.py` の `evaluate_feature_comprehensive` を SQL 結果に適用します。

- 陽性: `Class=1`。無ければ `predicted_Class=1` または `fraud_probability>=0.85`
- 優先特徴量: 監査変数 `V14`, `V17`, `V12`、続けて他の PCA / Amount（最大 12 変数）
- 強分離: `KS >= 0.40` かつ `|Cliff's Delta| >= 0.33`
- Z スコア差は `notes` で参考値と明記

### 4.6 Reflection (`reflection_step`)

LLM に JSON `{is_sufficient, score, critique}` を出させる。`score < 80` は不足とみなす。パース失敗時は `agent/parsing.py` がテキストを critique に残し、十分とはみなさない（黙って成功扱いにしない）。

不足かつ `retry_count < 2` → SQL Gen（critique 付き）。それ以外 → Output。

### 4.7 Output (`output_step`)

必須見出し:

1. 調査エグゼクティブサマリー
2. 検出された不正パターンの統計分析（V1〜V28）
3. 規程（ポリシー）照合結果
4. 推奨される即時是正アクション

是正 SQL は `ulb_fraud_detection_predictions` に対する **SELECT**。フェンスから抽出して `remediation_sql` に格納する。

---

## 5. データ契約

### 5.1 推論結果 `ulb_fraud_detection_predictions`

Dataform `daily_prediction.sqlx` が `ML.PREDICT` し、配列から `fraud_probability` を出します。

| 列 | 型 | 備考 |
| --- | --- | --- |
| Time, Amount, Class | FLOAT/INT | 公開データ由来 |
| V1–V28 | FLOAT64 | PCA 特徴 |
| Hour | INT64 | `Time` から算出 |
| Date | INT64 | 擬似日 1–50 |
| predicted_Class | INT64 | BQML 予測 |
| fraud_probability | FLOAT64 | P(不正)。UNNEST は不要 |

### 5.2 その他の参照先

- 元データ: `bigquery-public-data.ml_datasets.ulb_fraud_detection`
- 当日スライス: `dwh_prod.ulb_fraud_detection_Batch`
- 特徴量付与: `dwh_prod.ulb_fraud_detection_daily_converted`
- モデル: `dwh_prod.ulb_fraud_detection_model`（`BOOSTED_TREE_CLASSIFIER`, `ENABLE_GLOBAL_EXPLAIN`）
- 評価: `ulb_fraud_detection_evaluation_matrix`, `feature_separation`, `feature_importance`, `global_explain`, `local_explain`, `imbalance_metrics`, `psi`（定義は [EVALUATE.md](EVALUATE.md)）

エージェントは評価テーブルを SQL で読んで PR-AUC / PSI を引用してよいです。Analyzer は主に抽出行の特徴量統計を担当します。

### 5.3 ナレッジテーブル

```text
dwh_prod.fraud_investigation_knowledge
  doc_id STRING NOT NULL
  category STRING      -- POLICY | PCI_DSS | INCIDENT_CASE | UPLOADED
  title STRING
  content STRING
  source_uri STRING
  embedding ARRAY<FLOAT64>  -- 768-d, text-embedding-004
```

ベクトルインデックス（任意、行数が少ないと作成できない）:

```sql
CREATE VECTOR INDEX IF NOT EXISTS knowledge_vector_index
ON `{project}.dwh_prod.fraud_investigation_knowledge`(embedding)
OPTIONS(distance_type='COSINE', index_type='IVF');
```

インデックスが無くても `VECTOR_SEARCH` はブルートフォースで動きます（PoC の文書数では十分）。

---

## 6. 外部インタフェース

### 6.1 Python

```python
from agent.agent import FraudInvestigationAgent

agent = FraudInvestigationAgent(project_id="...", backend="local")
result = agent.query("fraud_probability>=0.85 の傾向を分析して")
```

Agent Engine は起動時に `set_up()` を一度呼び、その後 `query(user_query)` を呼びます。コンストラクタはシリアライズ可能なスカラーだけを持ち、クライアントは `set_up()` で開きます。

### 6.2 Streamlit

- 調査指示テキストエリア
- 実行後タブ: レポート / 計画と SQL と統計（XAI）/ 是正 SQL / 規程ヒット
- ファイルアップローダ: `.md` / `.txt` をチャンクしてナレッジへ

### 6.3 環境変数

| 変数 | 既定 | 説明 |
| --- | --- | --- |
| `PROJECT_ID` | （必須・local 時） | GCP プロジェクト |
| `LOCATION` | `asia-northeast1` | Vertex / BQ / Cloud Run |
| `DATASET_ID` | `dwh_prod` | データセット |
| `MODEL_NAME` | `gemini-2.0-flash` | 設計メモの 1.5-flash-002 が使えるなら上書き可 |
| `EMBEDDING_MODEL` | `text-embedding-004` | 埋め込み |
| `STAGING_BUCKET` | | Agent Engine 用 `gs://...` |
| `AGENT_BACKEND` | `local`（コンテナ） / `mock`（UI 開発） | 実行モード |
| `REASONING_ENGINE_RESOURCE_NAME` | | Path B 用リソース名 |

---

## 7. セキュリティと FinOps

- **最小権限:** `sa-fraud-agent` は `aiplatform.user`, `bigquery.jobUser`, データセットの `dataEditor`（ナレッジ UPSERT のため）, ステージングバケットの objectAdmin。パイプライン用 SA は変更しない。
- **書き込み範囲:** エージェント実行パスは SELECT のみ。ナレッジ投入スクリプト / UI アップロードだけが knowledge テーブルへ書く。
- **スキャン上限:** 10GiB billed。行数 200。
- **モデル:** 東京リージョンで使える Flash 系（既定 `gemini-2.0-flash`）。常時 GKE や Vertex AI Vector Search Endpoint は使わない。
- **Cloud Run:** `min_instances = 0`。PoC の UI は既定で未認証公開しない（`agent_ui_unauthenticated`）。

想定月額は設計メモどおり Gemini Flash + BQ 従量 + Agent Engine 従量で、予算 2 万円を大きく下回る見積もりです。実コストは Cloud Billing の予算アラートで監視してください（README 参照）。

---

## 8. テスト観点

`python -m unittest discover -s agent/tests -v`

| テスト | 確認すること |
| --- | --- |
| `test_sql_guard` | DML 拒否、LIMIT 付与 |
| `test_parsing` | SQL / JSON / 計画リスト抽出 |
| `test_knowledge` | チャンクとキーワード検索 |
| `test_analyzer` | フィクスチャで V14 が強分離 |
| `test_graph` | mock の E2E、SQL 失敗リトライ、Reflection 不足リトライ、DELETE 拒否後の回復 |

GCP 無しでグラフ全体が回ること。画面確認は `AGENT_BACKEND=mock` の Streamlit。

---

## 9. 既知の PoC 限界

1. 公開データに BIN / 会員 ID が無く、カードテストは Hour・Amount・PCA の代理指標に留まる。
2. Analyzer の PSI は抽出行の自己比較であり、学習スコア分布との比較は評価テーブル `ulb_fraud_detection_psi` を SQL で読む必要がある。
3. Agent Engine の Python 依存解決に失敗した場合は Path A（Cloud Run 内 LangGraph）で機能を証明する。
4. 規程の初期文書は設計どおり「未整備のため自動生成した参考資料」です。運用では UI から正式マニュアルを上げて差し替えてください。
