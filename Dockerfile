# Dockerfile for studyloversz-lichess-bot
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LICHHESS_TOKEN=${LICHHESS_TOKEN:-YOUR_SECRET_TOKEN_HERE}

# Install system deps and Stockfish engine
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates \
      stockfish \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app
COPY . /app

# Install Python deps: prefer requirements.txt if present
RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt ; \
    else \
      pip install --no-cache-dir python-chess requests ; \
    fi

# Expose port for health check
EXPOSE 8080

# Optional environment tuning - adjust these for stronger play
ENV SF_THREADS=${SF_THREADS:-2}
ENV SF_HASH=${SF_HASH:-128}
ENV SF_THINK_TIME=${SF_THINK_TIME:-0.5}
ENV SF_SKILL_LEVEL=${SF_SKILL_LEVEL:-20}

CMD ["python", "bot.py"]
