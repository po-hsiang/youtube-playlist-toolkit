# yt-mcp：歌單搜尋 MCP + REST 伺服器
# 映像只含 Python + 依賴；程式碼與設定（.env、playlists.toml、quota_state.json）
# 由 docker-compose 掛載專案目錄提供，與主機工具共用配額計數。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

# /audio 音訊抽取需要 ffmpeg（yt-dlp 下載後轉低碼率 Opus）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 先裝依賴（獨立快取層），再裝專案本體
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY youtube_toolkit ./youtube_toolkit
COPY playlists.toml ./
RUN uv sync --frozen --no-dev

EXPOSE 8765
CMD ["/opt/venv/bin/yt-mcp"]
