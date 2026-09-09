FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY daily_insert.py .

RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run Jobs run the script and stop the container on a successful exit.
CMD ["python", "daily_insert.py"]
