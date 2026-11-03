# Dockerfile - Run one controller as an unprivileged process with persistent local state.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Resolve tested runtime versions separately from the project's own source
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY licenses ./licenses
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && mkdir /state \
    && chown -R 10001:10001 /app /state

USER 10001:10001
EXPOSE 8787
CMD ["deploy-ops", "gate", "serve", "--config", "/config/controller.toml", "--host", "0.0.0.0"]
