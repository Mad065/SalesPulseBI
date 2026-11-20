FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SALESPULSEBI_DATA_DIR=/data \
    SALESPULSEBI_HOST=0.0.0.0 \
    SALESPULSEBI_PORT=8420

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir -e ".[xls]" && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8420

CMD ["salespulsebi", "serve", "--no-browser"]
