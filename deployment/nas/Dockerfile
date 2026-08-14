FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py database.py ./
COPY templates ./templates
RUN chmod -R a+rX /app

RUN useradd --system --create-home appuser
USER appuser

EXPOSE 8000
CMD ["gunicorn", "--workers", "2", "--bind", "127.0.0.1:8000", "app:app"]
