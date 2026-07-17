# yt-mcp 客戶端設定指南

`yt-mcp` 是歌單搜尋的 **MCP + REST 雙介面伺服器**（唯讀、API Key 認證），
以 Docker 容器 `yt-music-mcp` 長駐執行。本文件說明各種客戶端怎麼接上來。

## 端點總覽

| 介面 | 從主機（Windows） | 從其他容器（n8n / hermes） |
|------|------------------|---------------------------|
| MCP（Streamable HTTP） | `http://127.0.0.1:8765/mcp` | `http://yt-music-mcp:8765/mcp` |
| REST | `http://127.0.0.1:8765/...` | `http://yt-music-mcp:8765/...` |

- 埠只映射到宿主機 `127.0.0.1`，**不會暴露到區網**；容器間走 `ai-net` 內部網路。
- 無認證（僅限本機／內部網路使用，請勿轉發到公網）。

## 第一步：把你的容器接上 ai-net 網路

`docker compose up` 已建立名為 `ai-net` 的網路。讓 n8n 與 hermes 看得到伺服器：

```bash
docker network connect ai-net <n8n 容器名>
docker network connect ai-net <hermes 容器名>
```

> 一次性指令，容器重建（recreate）後要重下。若 n8n / hermes 是 docker compose 管理的，
> 建議改在它們的 compose 檔加上外部網路，重建也不會掉：
>
> ```yaml
> services:
>   n8n:
>     networks: [default, ai-net]
> networks:
>   ai-net:
>     external: true
> ```

接好後在容器內可用 `http://yt-music-mcp:8765` 直連（Docker 內部 DNS 解析服務名稱）。

## n8n 設定

1. 在 AI Agent 工作流中加入 **MCP Client Tool** 節點
2. **Endpoint / Server URL**：`http://yt-music-mcp:8765/mcp`
3. **Server Transport**：`HTTP Streamable`（若你的 n8n 版本只有 SSE 選項，請升級 n8n）
4. **Authentication**：None
5. 儲存後節點會自動探索到三個工具（search_songs / list_playlists / refresh_playlist），
   Agent 的 LLM 即可自行決定何時呼叫

> 備案：不想用 MCP 節點的話，用 **HTTP Request** 節點打 REST 端點也行（見下方）。

## Hermes（自製 agent）設定

通用 MCP client 設定（多數框架吃這個格式）：

```json
{
  "mcpServers": {
    "yt-music-search": {
      "type": "streamable-http",
      "url": "http://yt-music-mcp:8765/mcp"
    }
  }
}
```

自己寫 client 的話（Python，官方 SDK `mcp`）：

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://yt-music-mcp:8765/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search_songs", {"keyword": "Monsters"})
```

## MCP 工具參考

| 工具 | 參數 | 回傳 |
|------|------|------|
| `search_songs` | `keyword`（必填，≥2 字元）、`playlist`（選填，留空＝搜全部）、`limit`（預設 50） | `{keyword, searched_playlists, total_matches, returned, results: [{playlist, position, title, channel, views, url}]}` |
| `list_playlists` | 無 | 全部清單名稱與快取狀態（歌曲數、上次載入時間） |
| `refresh_playlist` | `playlist`（留空＝重抓所有已快取清單） | `{refreshed: {清單名: 歌曲數}}` |

清單名稱以 [playlists.toml](../playlists.toml) 為準（例：`YTMusic`、`Japanese`、`BGM / OST`）。

## REST 端點（非 AI 服務用）

| 端點 | 說明 |
|------|------|
| `GET /health` | 存活檢查與已快取清單 |
| `GET /playlists` | 同 `list_playlists` |
| `GET /search?q=<關鍵字>&playlist=<清單名>&limit=<n>` | 同 `search_songs`；`playlist` 可省略 |
| `GET /refresh?playlist=<清單名>` | 同 `refresh_playlist` |

範例（清單名含空格要 URL 編碼）：

```bash
curl "http://127.0.0.1:8765/search?q=Monsters&playlist=YTMusic&limit=5"
curl --get "http://127.0.0.1:8765/search" --data-urlencode "q=cover" --data-urlencode "playlist=Covers (Chinese)"
```

錯誤回應：關鍵字太短 → 400；清單名稱不存在 → 404（訊息會列出可用名稱）。

## 快取與配額行為

- 清單**首次被查詢時**才載入（YTMusic 約 42 units），之後常駐記憶體，
  **查詢本身不耗 YouTube 配額**、毫秒級回應
- 快取 TTL 預設 **6 小時**（`.env` 的 `MCP_CACHE_TTL_MINUTES` 可調）；
  剛加入清單的新歌要等 TTL 過期或呼叫 `refresh_playlist` 才查得到
- 所有抓取經 QuotaManager 記帳，與主機工具共用 `quota_state.json` 合併計算，
  軟上限 8,000 熔斷照常生效

## 維運

```bash
docker compose up -d --build   # 建置＋啟動（改了依賴後要 --build）
docker compose restart         # 只改程式碼／playlists.toml 後重啟即可（專案目錄是掛載的）
docker compose logs -f         # 看日誌
docker compose down            # 停止（ai-net 網路會一併移除，重啟後容器要重新 connect）
```

安全備忘：伺服器**唯讀**（不暴露排序／清除等寫入功能）、映像不含 `.env` 與 `secrets/`
（執行期由掛載提供、OAuth 憑證被遮罩不進容器）、埠不對區網開放。
