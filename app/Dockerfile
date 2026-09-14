FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    AGENT_BACKEND=local

WORKDIR /app

COPY agent/requirements.txt /tmp/agent-requirements.txt
COPY app/requirements.txt /tmp/app-requirements.txt
RUN pip install --no-cache-dir -r /tmp/app-requirements.txt

COPY agent /app/agent
COPY evaluate /app/evaluate
COPY knowledge /app/knowledge
COPY app/app.py /app/app.py
COPY app/.streamlit /app/.streamlit

ENV PYTHONPATH=/app:/app/evaluate

# 非 root。Cloud Run の推奨（権限昇格の影響範囲を狭める）。
RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true"]
