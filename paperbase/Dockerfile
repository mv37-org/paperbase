FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Native libraries required by docling's PDF pipeline. docling imports cv2
# (opencv-python, not -headless), which dlopens libGL + libxcb at runtime;
# without these the worker dies on first PDF with
# "ImportError: libxcb.so.1: cannot open shared object file" and every
# retry fails identically.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxcb1 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Pre-install heavy dependencies in their own layer so iterating on src
# doesn't invalidate the ML libraries (torch, transformers, docling) which
# take the longest to download. This file is a best-effort cache seed; the
# authoritative dependency list is pyproject.toml, resolved in the next step.
COPY requirements.docker.txt ./
RUN python -m pip install -r requirements.docker.txt

COPY pyproject.toml README.md ./
COPY src ./src

# Resolve the full dependency closure from pyproject.toml (no --no-deps!) so
# new entries don't silently fall off the image. pip is a no-op for anything
# already satisfied by the requirements layer above.
RUN python -m pip install .

ENV PORT=8080

EXPOSE 8080

CMD ["paperbase-api"]
