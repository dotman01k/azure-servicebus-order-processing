FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid 10001 --no-create-home worker
COPY orders ./orders
USER 10001:10001
CMD ["python", "-m", "orders.worker"]
