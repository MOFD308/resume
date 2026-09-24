FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY market_digest ./market_digest
EXPOSE 8000
CMD ["python", "-m", "market_digest", "run"]
