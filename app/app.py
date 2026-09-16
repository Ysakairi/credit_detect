"""不正調査エージェントの Streamlit コンソール（Path A / Path B の操作面）。

【Agent Engine 上の位置づけ】
アナリスト向け UI。backend=local/mock は同一プロセスで ``FraudInvestigationAgent.query()``
を呼び（Path A）、backend=reasoning_engine はデプロイ済み Agent Engine の遠隔
``query(user_query=...)`` を呼ぶ（Path B）。戻り値のキーはどちらも agent.py のサブセット。
ナレッジアップロードはグラフ外の書き込み経路。調査実行の SELECT 専用契約を破らないため。

【主な関数構成】
- _config_from_sidebar: 接続先と backend 切替
- _query_reasoning_engine: Path B（Agent Engine RPC）
- _run_local: Path A（プロセス内 LangGraph）
- _ingest_upload: マニュアルのチャンク投入
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from agent.agent import FraudInvestigationAgent
from agent.backends import build_deps, load_mock_knowledge
from agent.config import AgentConfig, RuntimeDeps
from agent.tools.knowledge_ingest import documents_from_text

st.set_page_config(page_title="AI Fraud Investigation Console", layout="wide")
st.title("Autonomous Fraud Investigation Agent")
st.caption("Vertex AI × LangGraph × BigQuery  /  credit_detect PoC")


def _config_from_sidebar() -> AgentConfig:
    """サイドバーから AgentConfig を組み立て、再実行ごとに環境変数と画面入力を同期する。

    mock を既定にするのは、認証前の画面確認で Gemini/BQ を叩かないため。

    Returns:
        画面入力を反映した AgentConfig。
    """
    with st.sidebar:
        st.header("接続設定")
        backend = st.selectbox(
            "バックエンド",
            options=["mock", "local", "reasoning_engine"],
            index=["mock", "local", "reasoning_engine"].index(
                os.environ.get("AGENT_BACKEND", "mock")
            )
            if os.environ.get("AGENT_BACKEND", "mock") in {"mock", "local", "reasoning_engine"}
            else 0,
            help="mock は GCP なしでグラフをデモします。local は Cloud Run 内で Gemini+BQ。reasoning_engine は Agent Engine を呼び出します。",
        )
        project_id = st.text_input(
            "GCP Project ID", value=os.environ.get("PROJECT_ID", "local-mock-project")
        )
        location = st.text_input("Location", value=os.environ.get("LOCATION", "asia-northeast1"))
        dataset = st.text_input("Dataset", value=os.environ.get("DATASET_ID", "dwh_prod"))
        model_name = st.text_input(
            "Gemini model", value=os.environ.get("MODEL_NAME", "gemini-2.0-flash")
        )
        engine_name = st.text_input(
            "Reasoning Engine resource name",
            value=os.environ.get("REASONING_ENGINE_RESOURCE_NAME", ""),
        )
        st.markdown(
            """
**参照テーブル**
- `dwh_prod.ulb_fraud_detection_predictions`
- `dwh_prod.ulb_fraud_detection_model`
- `bigquery-public-data.ml_datasets.ulb_fraud_detection`
            """
        )
    return AgentConfig(
        project_id=project_id,
        location=location,
        dataset=dataset,
        model_name=model_name,
        backend=backend,
        reasoning_engine_resource=engine_name,
    )


def _mock_store():
    """セッションにメモリナレッジを保持し、アップロード後の再検索がリロードで消えないようにする。

    Returns:
        InMemoryKnowledgeStore（初回は同梱マニュアル入り）。
    """
    if "mock_knowledge" not in st.session_state:
        st.session_state.mock_knowledge = load_mock_knowledge()
    return st.session_state.mock_knowledge


def _query_reasoning_engine(resource_name: str, user_query: str) -> dict:
    """Path B: デプロイ済み Agent Engine の query() だけを呼び、UI コンテナで Gemini/BQ を開かない。

    Args:
        resource_name: REASONING_ENGINE_RESOURCE_NAME。
        user_query: 調査指示。リモートの initial_state になる。

    Returns:
        FraudInvestigationAgent.query と同じキーの dict。
    """
    import vertexai
    from vertexai.preview import reasoning_engines

    vertexai.init()
    remote = reasoning_engines.ReasoningEngine(resource_name)
    return remote.query(user_query=user_query)


def _run_local(config: AgentConfig, user_query: str) -> dict:
    """Path A: 同一プロセスで LangGraph を invoke する。Cloud Run 既定経路。

    mock 時だけ session の knowledge を差し、アップロード資料がグラフに見えるようにする。

    Args:
        config: backend を含む実行設定。
        user_query: 調査指示。

    Returns:
        query() の結果 dict。
    """
    deps = build_deps(config)
    if config.backend == "mock":
        deps = RuntimeDeps(
            config=config,
            llm=deps.llm,
            sql_runner=deps.sql_runner,
            knowledge=_mock_store(),
        )
    agent = FraudInvestigationAgent(
        project_id=config.project_id,
        location=config.location,
        model_name=config.model_name,
        dataset=config.dataset,
        backend=config.backend,
        config=config,
        deps=deps,
    )
    return agent.query(user_query)


def _ingest_upload(config: AgentConfig, filename: str, raw: bytes) -> int:
    """アップロードをチャンクしてナレッジへ書く。調査グラフの SELECT 専用契約の外側。

    Args:
        config: mock ならメモリ、それ以外は BQ テーブル。
        filename: source_uri と title の元。doc_id 安定化に使う。
        raw: ファイルバイト。UTF-8 以外は置換し、投入全体を落とさない。

    Returns:
        upsert したチャンク数。
    """
    text = raw.decode("utf-8", errors="replace")
    docs = documents_from_text(text, title=Path(filename).stem, source_uri=filename)
    if config.backend == "mock":
        return _mock_store().upsert(docs)
    from agent.tools.bq_vector_search import BigQueryKnowledgeStore

    return BigQueryKnowledgeStore(config).upsert(docs)


config = _config_from_sidebar()

st.subheader("ナレッジ登録（ベクトル化）")
uploaded = st.file_uploader(
    "調査マニュアル / 規程 (Markdown / テキスト)",
    type=["md", "txt"],
    accept_multiple_files=True,
)
if uploaded and st.button("アップロード資料をベクトル化して登録"):
    total = 0
    for item in uploaded:
        total += _ingest_upload(config, item.name, item.getvalue())
    st.success(f"{total} チャンクをナレッジストアへ登録しました。")

query = st.text_area(
    "調査指示",
    height=110,
    value=(
        "直近の推論データにおいて、不正確率が 0.85 以上かつ Amount が 200 ドル以上の"
        "トランザクションを調査し、PCI-DSS / 社内規程に照らした遮断ルールを提案して。"
    ),
)

if st.button("自律調査を開始", type="primary"):
    with st.spinner("Plan → RAG → Text-to-SQL → Analysis → Reflection …"):
        try:
            if config.backend == "reasoning_engine":
                # Path B はリソース名が無いとローカルグラフにサイレントフォールバックせず、誤課金先を防ぐ。
                if not config.reasoning_engine_resource:
                    st.error("Reasoning Engine のリソース名を入力してください。")
                    st.stop()
                response = _query_reasoning_engine(config.reasoning_engine_resource, query)
            else:
                response = _run_local(config, query)
        except Exception as exc:
            st.exception(exc)
            st.stop()

    tab1, tab2, tab3, tab4 = st.tabs(
        ["調査レポート", "実行プロセス（XAI）", "即時是正SQL", "参照規程"]
    )
    with tab1:
        st.markdown(response.get("final_report") or "_レポートなし_")
    with tab2:
        st.subheader("調査計画")
        for i, step in enumerate(response.get("plan") or [], start=1):
            st.write(f"{i}. {step}")
        st.subheader("生成 SQL")
        st.code(response.get("generated_sql") or "", language="sql")
        if response.get("sql_error"):
            st.warning(response.get("sql_error"))
        st.subheader("統計サマリー")
        st.json(response.get("statistical_summary") or {})
        st.subheader("Reflection")
        st.info(
            f"score={response.get('reflection_score')} / retries={response.get('retry_count')}\n\n"
            f"{response.get('critique') or ''}"
        )
    with tab3:
        st.code(response.get("remediation_sql") or "", language="sql")
        st.caption("PoC では DML を実行しません。Dataform への提案としてコピーしてください。")
    with tab4:
        for policy in response.get("retrieved_policies") or []:
            with st.expander(policy.get("title") or policy.get("doc_id") or "policy"):
                st.caption(
                    f"{policy.get('category')} / distance={policy.get('distance')}"
                )
                st.write(policy.get("content"))
