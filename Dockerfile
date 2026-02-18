FROM python:3.12-slim

# Install system dependencies
# - ffmpeg: audio transcoding
# - curl + unzip: download/extract Deno
# - git: required by pip to install fluxer.py from GitHub
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (JavaScript runtime required for YouTube signature solving)
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

# Copy source code (from local directory or GitHub build context)
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download bgutil-pot binary (YouTube PO token generator, Linux x86_64)
RUN curl -fsSL \
    "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64" \
    -o bgutil-pot && chmod +x bgutil-pot

# Persistent data directory for guild settings
RUN mkdir -p /data
ENV GUILD_SETTINGS_FILE=/data/guild_settings.json

CMD ["python", "main.py"]
