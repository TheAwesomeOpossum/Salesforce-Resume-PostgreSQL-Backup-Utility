FROM python:3.12-slim

# Create non-root user
RUN useradd -m -u 1001 syncuser

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create logs directory owned by syncuser
RUN mkdir -p /app/logs && chown -R syncuser:syncuser /app

USER syncuser

CMD ["python", "-m", "src.sync"]
