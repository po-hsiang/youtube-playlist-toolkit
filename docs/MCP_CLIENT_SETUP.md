# yt-mcp 客戶端設定指南

`yt-mcp` 是 YouTube 唯讀查詢的 **MCP + REST 雙介面伺服器**（API Key 認證），
以 Docker 容器 `yt-music-mcp` 長駐執行。本文件說明各種客戶端怎麼接上來。

**伺服器提供兩類來源完全不同的資料，別混用：**

| | 資料來源 | 工具／端點 | 配額 |
|---|---------|-----------|------|
| 🎵 | **使用者自己的播放清單** | `search_songs`／`random_song`／`/search`／`/random` | 載入後查詢 0 units |
| 🔥 | **YouTube 公開資料**（發燒榜、單片查詢） | `trending_videos`／`get_video_info`／`/trending`／`/video/{id}` | 即時查詢，1 unit／次 |
| 📝 | **影片字幕全文**（走網頁端資料） | `get_video_transcript`／`/transcript/{id}` | **0 配額**（不經 Data API） |
| 🔊 | **低碼率音訊抽取**（yt-dlp + ffmpeg） | `/audio/{id}`（**REST 專屬**，無 MCP 工具） | **0 配額**（不經 Data API） |

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
| `search_songs` | `keyword`（必填，≥2 字元）、`playlist`（選填，留空＝搜全部）、`limit`（預設 50） | `{keyword, searched_playlists, total_matches, returned, results: [{playlist, position, title, channel, views, url, matched_on}]}`；0 筆時另附 `hint` |
| `random_song` | `playlist`（選填，留空＝預設清單、`*`＝全部）、`count`（1～10，預設 1）、`keyword`（選填，≥2 字元） | `{playlists, keyword, candidates, returned, songs: [{playlist, position, title, channel, views, url, matched_on}]}`；有給關鍵字卻 0 筆時另附 `hint` |
| `trending_videos` | `category`（預設 all）、`limit`（1～50，預設 3）、`region`（預設 TW） | `{source, region, category, returned, videos: [{rank, title, channel, views, likes, published_at, duration, duration_seconds, is_live, category_id, url}]}` |
| `get_video_info` | `video_id`（必填，11 碼） | `{video_id, title, channel, published_at, duration, duration_seconds, is_live, views, thumbnail_url, url}`；錯誤回 `{error: "INVALID_VIDEO_ID"\|"VIDEO_NOT_FOUND"}` |
| `get_video_transcript` | `video_id`（必填，11 碼）、`max_chars`（預設 8000，0＝不截斷） | `{video_id, language, language_code, is_auto_generated, text, char_count, truncated}`；錯誤回 `{error: "INVALID_VIDEO_ID"\|"VIDEO_NOT_FOUND"\|"NO_TRANSCRIPT"\|"TRANSCRIPT_UPSTREAM_ERROR"}` |
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
| `GET /video/{video_id}` | 同 `get_video_info`；格式錯 400 `INVALID_VIDEO_ID`、查無（含私人影片）404 `VIDEO_NOT_FOUND` |
| `GET /transcript/{video_id}?max_chars=<n>` | 同 `get_video_transcript`；**REST 預設不截斷**（MCP 預設截 8000） |
| `GET /audio/{video_id}` | 低碼率音訊抽取（OGG/Opus 二進位串流）；**REST 專屬**，詳見下方 🔊 章節 |
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

### 🌏 跨語言搜尋（v0.14.0）

歌單約七成曲目來自 YouTube 自動生成的「**- Topic**」頻道，歌名與頻道名只有羅馬字。
比對範圍已納入影片標籤（藝人的母語名多半在裡面），所以中文／日文／韓文藝人名
大多查得到。這份標籤資料本來就在既有的 API 回應裡，**不增加任何配額**。

| 查詢 | v0.13 | v0.14 |
|------|------:|------:|
| `周興哲` | 0 | 2 |
| `五月天` | 0 | 2 |
| `陳奕迅` | 0 | 2 |
| `林俊傑` | 0 | 3 |
| `ラッドウィンプス` | 0 | 8 |
| `あいみょん` | 0 | 5 |
| `아이유` | 0 | 2 |
| `周杰倫` | 1 | 16 |
| `米津玄師` | 1 | 17 |

**給 Agent 的兩個要點：**

1. 每筆結果的 `matched_on` 標示命中欄位——`title`／`channel` 是直接命中（可信），
   `tag` 是標籤命中。標籤是隱藏欄位，部分頻道會塞宣傳性標籤（實測有影片掛了 60 個
   標籤、包含一整排他人藝名），所以 `tag` 命中**可能是相關作品而非該藝人本人**，
   回覆使用者時請留意。直接命中一律排在標籤命中之前。
2. 查無結果時回傳 `hint` 欄位。**看到 0 筆不要直接回覆「沒有收錄」**——
   先改用該藝人的英文或羅馬拼音名再查一次（例：田馥甄→Hebe），兩種寫法都沒命中才下結論。


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

## 🎬 單片中繼資料（`/video/{video_id}`）

給下游機器人做**時長預檢、直播婉拒、embed 呈現**。任何公開影片都可查，
不限於使用者的播放清單。1 unit／次、不快取（要判斷「現在」是不是直播）。

```bash
curl "http://127.0.0.1:8765/video/dQw4w9WgXcQ"
```

```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
  "channel": "Rick Astley",
  "published_at": "2009-10-25T06:57:33Z",
  "duration": "PT3M34S",
  "duration_seconds": 214,
  "is_live": false,
  "views": 1800341708,
  "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

- **欄位名是跨服務契約**，不會改名；機器人直接依名取值
- `is_live` 判定：`liveBroadcastContent == "live"` 或時長為 0（進行中直播的
  duration 可能是 `P0D`）。實測進行中的 lofi girl 直播回
  `{"duration": "P0D", "duration_seconds": 0, "is_live": true}`；
  已結束的直播存檔則正確回 `is_live: false`（時長會是實際直播長度，可能長達數十天，
  時長預檢記得一併把關）
- `thumbnail_url` 取解析度最高的可用縮圖（maxres → standard → high → medium → default）
- 錯誤：格式不合法（非 11 碼 `[A-Za-z0-9_-]`）→ 400 `{"error": "INVALID_VIDEO_ID"}`；
  查無影片或私人影片 → 404 `{"error": "VIDEO_NOT_FOUND"}`

## 📝 影片字幕全文（`/transcript/{video_id}`）

抓字幕純文字給 LLM 摘要影片內容。走 YouTube 網頁端資料（youtube-transcript-api），
**不經 Data API、0 配額**。

```bash
curl "http://127.0.0.1:8765/transcript/9lVPAWLWtWc"                # 全文
curl "http://127.0.0.1:8765/transcript/9lVPAWLWtWc?max_chars=8000" # 截斷
```

```json
{
  "video_id": "9lVPAWLWtWc",
  "language": "Chinese (Traditional) - Official",
  "language_code": "zh-Hant",
  "is_auto_generated": false,
  "text": "已經忘了嗎 坐在夏日的樹蔭下，我們把冰放進口中等待風起 …",
  "char_count": 457,
  "truncated": false
}
```

- **欄位名是跨服務契約**，不會改名
- 字幕軌自動挑選：人工上傳優先，語言依 `zh-Hant-TW → zh-Hant → zh-TW → en`，
  沒有再退**任一**人工字幕；都沒有才用自動生成字幕（同語言優先序 → 任一）
- `max_chars`：超過即截斷並回 `truncated: true`；**`char_count` 一律是完整字幕長度**，
  下游才知道全文有多少。REST 預設不截斷；MCP 工具預設截 8000（避免塞爆 agent context），
  傳 `max_chars: 0` 可關掉
- 字幕全文可能長達數十萬字元（長片／直播存檔），餵 LLM 前請自行斟酌 max_chars
- 錯誤：格式不合法 → 400 `INVALID_VIDEO_ID`；影片不存在／私人 → 404 `VIDEO_NOT_FOUND`；
  無任何字幕或字幕停用 → 404 `NO_TRANSCRIPT`；被 YouTube 封鎖／限流 →
  502 `TRANSCRIPT_UPSTREAM_ERROR`（**可稍後重試**，機器人端建議退避）

## 🔊 低碼率音訊抽取（`/audio/{video_id}`）

給「影片快速摘要」的 fallback：影片**沒有 CC 字幕**（`/transcript` 回 404 `NO_TRANSCRIPT`）時，
由 n8n 呼叫此端點抽出低碼率純音訊，上傳給 Gemini 做語音轉錄＋摘要。
yt-dlp 下載 bestaudio 後以 ffmpeg 轉檔，**不經 Data API、0 配額**。

```bash
curl -o audio.ogg "http://127.0.0.1:8765/audio/9lVPAWLWtWc"
```

- 成功 → `200`，`Content-Type: audio/ogg` 二進位串流（**OGG/Opus、32kbps、單聲道**，
  約 14.4 MB／小時——Gemini 支援的 `audio/ogg` 格式）
- **REST 專屬、不設 MCP 工具**：二進位輸出對 AI agent 沒有意義
- **耗時提醒**：實測 4 分鐘影片約 6 秒、16 分鐘影片約 15 秒（大約每分鐘影片耗 1 秒）
- **逾時三層瀑布（2026-08-05 定案）**：bot 200 秒 → n8n 190 秒 → 本服務 180 秒。
  伺服器端整體逾時 180 秒（含探測），超過即回 502——最內層先放棄，
  上游才收得到明確錯誤而不是斷線。**n8n 呼叫端請設 190 秒**
- 刻意不快取：一部影片只該叫一次，重複呼叫會重新抽取
- 錯誤（JSON 格式 `{"error": 代碼}`）：
  - 400 `INVALID_VIDEO_ID`：video_id 不是 11 碼
  - 400 `LIVE_STREAM`：直播中或即將首播（沒有完整音訊可抽）
  - 404 `VIDEO_NOT_FOUND`：影片不存在／私人
  - 413 `AUDIO_TOO_LONG`：時長超過上限（`.env` 的 `AUDIO_MAX_DURATION_SECONDS`，
    預設 4200 秒＝70 分鐘）；本服務照樣回 413，「不回應使用者」由機器人端自行決定
  - 502 `AUDIO_EXTRACT_FAILED`：yt-dlp／ffmpeg 失敗或逾時（**可稍後重試**；
    若持續發生，通常是 YouTube 改版、需要升級 yt-dlp——見伺服器端維運文件）
- 已知限制：年齡限制影片無登入憑證抽不到，會落在 502

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
