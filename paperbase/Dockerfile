FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.docker.txt ./
RUN python -m pip install -r requirements.docker.txt

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-deps .

ENV PORT=8080

EXPOSE 8080

CMD ["paperbase-api"]
