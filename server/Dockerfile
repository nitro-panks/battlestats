# server/Dockerfile
FROM python:3.12-slim

WORKDIR /usr/src/app

ARG INSTALL_AGENTIC_DEPS=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-agentic.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_AGENTIC_DEPS" = "1" ]; then pip install --no-cache-dir -r requirements-agentic.txt; fi

COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput

CMD ["gunicorn", "--config", "gunicorn.conf.py", "battlestats.wsgi:application"]
