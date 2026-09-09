# Cloud Run Job 用イメージ。HTTP サーバは持たず、終了コード 0 で成功とみなす。
# python:3.10-slim はビルド時間と攻撃面を抑えるため（debian フルイメージは不要）。
FROM python:3.10-slim

# PYTHONUNBUFFERED: Cloud Logging がバッファ遅延なく stdout JSON を拾うため。
# PYTHONDONTWRITEBYTECODE: 読み取り専用に近いコンテナで .pyc を書かない。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY daily_insert.py .

# 非 root。Cloud Run の推奨（権限昇格の影響範囲を狭める）。
RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run Jobs run the script and stop the container on a successful exit.
CMD ["python", "daily_insert.py"]
