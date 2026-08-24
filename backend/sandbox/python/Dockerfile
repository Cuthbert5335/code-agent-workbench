FROM python:3.13-alpine

RUN python -m pip install --no-cache-dir pytest ruff mypy

USER 65534:65534
WORKDIR /workspace
