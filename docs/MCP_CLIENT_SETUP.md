# yt-mcp 客戶端設定指南

`yt-mcp` 是 YouTube 唯讀查詢的 **MCP + REST 雙介面伺服器**（API Key 認證），
以 Docker 容器 `yt-music-mcp` 長駐執行。本文件說明各種客戶端怎麼接上來。

**伺服器提供兩類來源完全不同的資料，別混用：**

| | 資料來源 | 工具／端點 | 配額 |
|---|---------|-----------|------|
| 🎵 | **使用者自己的播放清單** | `search_songs`／`random_song`／`/search`／`/random` | 載入後查詢 0 units |
| 🔥 | **YouTube 官方公開榜單**（發燒影片） | `trending_videos`／`/trending` | 即時查詢，1 unit／次 |

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
5. 儲存後節點會自動探索到四個工具（search_songs / random_song / list_playlists / refresh_playlist），
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
| `random_song` | `playlist`（選填，留空＝預設清單、`*`＝全部）、`count`（1～10，預設 1）、`keyword`（選填，≥2 字元） | `{playlists, keyword, candidates, returned, songs: [{playlist, position, title, channel, views, url}]}` |
| `trending_videos` | `category`（預設 all）、`limit`（1～50，預設 3）、`region`（預設 TW） | `{source, region, category, returned, videos: [{rank, title, channel, views, likes, published_at, duration, duration_seconds, is_live, category_id, url}]}` |
| `list_playlists` | 無 | 全部清單名稱與快取狀態（歌曲數、上次載入時間） |
| `refresh_playlist` | `playlist`（留空＝重抓所有已快取清單） | `{refreshed: {清單名: 歌曲數}}` |

清單名稱以 [playlists.toml](../playlists.toml) 為準（例：`YTMusic`、`Japanese`、`BGM / OST`）。
`songs` 與 `results` 是**同一種單曲格式**，客戶端只需要認得一種形狀。

## REST 端點（非 AI 服務用）

| 端點 | 說明 |
|------|------|
| `GET /health` | 存活檢查與已快取清單 |
| `GET /playlists` | 同 `list_playlists` |
| `GET /search?q=<關鍵字>&playlist=<清單名>&limit=<n>` | 同 `search_songs`；`playlist` 可省略 |
| `GET /random?playlist=<清單名>&count=<n>&q=<關鍵字>` | 同 `random_song`；三個參數都可省略 |
| `GET /trending?category=<類別>&limit=<n>&region=<國碼>` | 同 `trending_videos`；三個參數都可省略 |
| `GET /refresh?playlist=<清單名>` | 同 `refresh_playlist` |

範例（清單名含空格要 URL 編碼）：

```bash
curl "http://127.0.0.1:8765/search?q=Monsters&playlist=YTMusic&limit=5"
curl --get "http://127.0.0.1:8765/search" --data-urlencode "q=cover" --data-urlencode "playlist=Covers (Chinese)"

curl "http://127.0.0.1:8765/random"                      # 從 YTMusic 抽 1 首
curl "http://127.0.0.1:8765/random?count=3"              # 抽 3 首不重複
curl "http://127.0.0.1:8765/random?playlist=BGM%20%2F%20OST"
curl --get "http://127.0.0.1:8765/random" --data-urlencode "q=ヨルシカ"   # 只從命中的歌抽
```

錯誤回應：關鍵字太短、`count` 不是整數 → 400；清單名稱不存在 → 404（訊息會列出可用名稱）。
**候選為空不是錯誤**——回 200 且 `songs: []`，客戶端檢查陣列是否為空即可。

## 🔥 發燒影片榜（`/trending`）

YouTube 官方 Trending 榜，**公開的地區榜單，與使用者的播放清單無關**。
預設回**台灣榜前 3 名**（`regionCode=TW` 拿到的就只有台灣榜，不是全球榜）。

```bash
curl "http://127.0.0.1:8765/trending"                    # TW 全類別前 3
curl "http://127.0.0.1:8765/trending?category=music"     # TW 音樂類前 3
curl "http://127.0.0.1:8765/trending?category=gaming&limit=5"
curl "http://127.0.0.1:8765/trending?region=JP&category=music"
```

類別：`all`／`music`／`gaming`／`film`／`sports`／`comedy`／`entertainment`／`news`／`tech`，
也可直接給 `videoCategoryId` 數字。預設地區可用 `.env` 的 `TRENDING_REGION` 改。

回傳範例（實測）：

```json
{
  "source": "YouTube 發燒影片（官方公開榜單，非使用者的播放清單）",
  "region": "TW", "category": "music", "returned": 3,
  "videos": [{
    "rank": 1,
    "title": "JENNIE - Less than a Lover (Official Video)",
    "channel": "JennieRubyJaneVEVO",
    "views": 14386857, "likes": 1095026,
    "published_at": "2026-07-24",
    "duration": "3:39", "duration_seconds": 219,
    "is_live": false, "category_id": "10",
    "url": "https://youtu.be/..."
  }]
}
```

- `likes` 可能是 `null`（頻道隱藏讚數），**不要當成 0**
- `duration_seconds` 與 `is_live` 是給你濾掉實況存檔用的——發燒榜常出現數小時的直播回放
  （實測遊戲類第 2 名就是 `5:41:03` 的 LCK 賽事）
- **不快取**：1 unit／次，榜單約 15 分鐘換一批，即時查最準

錯誤回應：類別名稱打錯 → 400（會列出可用名稱）；
**該地區沒有這個類別的榜** → 404（實測 TW 的類別 29 就沒有榜）。

## 機器人「隨機點歌」整合（REST）

`/random` 就是為這個情境做的：機器人**不需要自己維護歌單、不需要 API Key、不需要記憶體快取**，
歌單更新也會在 TTL 內自動生效。

```python
import requests

YT_API = "http://yt-music-mcp:8765"   # 容器內用服務名；主機上改 http://127.0.0.1:8765
LOAD_FAIL_MESSAGE = "歌單暫時拿不到，稍後再試 🙏"


class SongPicker:
    # (連線, 讀取)：冷快取的第一次呼叫伺服器要載入整份歌單，實測約 9 秒，讀取逾時要放寬
    TIMEOUT = (3, 30)

    def choose_one_song(self, keyword: str = "") -> str:
        params = {"count": 1}
        if keyword:
            params["q"] = keyword
        try:
            resp = requests.get(f"{YT_API}/random", params=params, timeout=self.TIMEOUT)
            resp.raise_for_status()
            songs = resp.json()["songs"]
        except requests.RequestException as e:
            print(f"[{self.__class__.__name__}] 取歌失敗，之後使用時會再重試：{e}")
            return LOAD_FAIL_MESSAGE
        if not songs:
            return LOAD_FAIL_MESSAGE
        return songs[0]["url"]

    def warm_up(self) -> None:
        """機器人啟動時呼叫：先讓伺服器把歌單載進快取，第一位使用者就不用等那 9 秒。"""
        try:
            requests.get(f"{YT_API}/random", timeout=(3, 60))
        except requests.RequestException as e:
            print(f"暖機失敗（不影響後續使用，屆時第一次點歌會慢一點）：{e}")
```

對照舊寫法：`ensure_loaded()` 那套「本地載入歌單 ＋ 失敗留待下次重試」不再需要——
載入、快取、重試、配額記帳全部由伺服器負責，機器人端只剩一次 HTTP GET。

實測效能：冷快取首呼 **8.5 秒**（載入 1,021 首），之後每次 **約 65 毫秒**、**0 配額**。

**想避免連續抽到同一首**：`/random` 每次獨立抽選，不記得抽過什麼（無狀態才能讓多個 agent 共用）。
需要「最近不重複」的話，兩種做法擇一：

```python
# A. 一次抽一批，機器人端慢慢發（最省事）
songs = requests.get(f"{YT_API}/random", params={"count": 10}, timeout=(3, 30)).json()["songs"]

# B. 機器人端記最近播過的 URL，抽到重複就重抽
recent = collections.deque(maxlen=20)
```

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
