import os
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
DESTINATION_TABLE = os.environ.get("DESTINATION_TABLE", "your_dataset.ulb_fraud_detection_Batch") # 投入先をBatchテーブルに変更

def run_ingestion():
    """
    公開データセットからデータを取得し、擬似日付(Date)を付与して
    当日のデータのみを抽出し、バッチ用テーブルを上書き(TRUNCATE)する処理
    """
    client = bigquery.Client(project=PROJECT_ID)

    query = """
        SELECT *
        FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`
    """
    print("Fetching data from public dataset...")
    df = client.query(query).to_dataframe()

    # Timeで昇順ソートし、インデックスをリセット
    df = df.sort_values(by='Time').reset_index(drop=True)

    # 1〜50の連番を生成
    print("Generating pseudo Date column...")
    df['Date'] = (df.index % 50) + 1
    df['Date'] = df['Date'].astype('int64')

    # 実行日の取得
    target_date_str = os.environ.get("TARGET_DATE")
    if target_date_str:
        target_date = int(target_date_str)
        print(f"Target date specified by environment variable: {target_date}")
    else:
        jst = timezone(timedelta(hours=9))
        target_date = datetime.now(jst).day
        print(f"Target date from current JST date: {target_date}")

    # 当日のデータのみを抽出
    print(f"Filtering data for Date == {target_date}...")
    df_filtered = df[df['Date'] == target_date].copy()

    if df_filtered.empty:
        print(f"No data found for Date == {target_date}. Exiting.")
        return

    # 【重要】月またぎの重複を防ぐため、日次の一時テーブルとして上書き(TRUNCATE)する
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
    )

    print(f"Loading data into {DESTINATION_TABLE}...")
    job = client.load_table_from_dataframe(
        df_filtered, 
        DESTINATION_TABLE, 
        job_config=job_config
    )
    
    job.result()
    print(f"Loaded {job.output_rows} rows into {DESTINATION_TABLE}.")

if __name__ == "__main__":
    try:
        run_ingestion()
    except Exception as e:
        print(f"Error during ingestion: {e}")
        raise e