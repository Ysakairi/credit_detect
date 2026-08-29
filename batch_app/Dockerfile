FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY daily_insert.py .

# Cloud Run Jobsは起動時にスクリプトを実行し、正常終了でコンテナを停止する
CMD ["python", "daily_insert.py"]