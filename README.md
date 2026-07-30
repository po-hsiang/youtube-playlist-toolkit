# YouTube API 播放清單工具組

一組以 **YouTube Data API v3** 打造的個人播放清單自動化管理工具，核心功能是「將 YouTube 播放清單依照 *頻道名稱 → 觀看次數* 自動重新排序」，並附帶重複歌曲偵測、歌單關鍵字搜尋、影片搜尋等輔助工具。

> 專案內建 **API 配額（Quota）管理機制**：每次 API 呼叫前先檢查累計成本，達到軟上限（8,000 units）即安全中止，避免把每日 10,000 units 的免費配額燒光。

---

## 功能總覽

| 工具 | 模組 | 認證方式 | 功能 |
|------|------|----------|------|
| 🎯 播放清單自動排序 | `youtube_toolkit/playlist_sorter.py` | OAuth 2.0 | **主力工具**。每天定時（預設 16:05）將 13 個播放清單依「頻道 A→Z、觀看數高→低」排序，並實際寫回 YouTube |
| 🔍 歌單關鍵字搜尋 | `youtube_toolkit/playlist_search.py` | API Key | 命令列搜尋歌名／頻道；優先走 yt-mcp 記憶體快取（**0 配額、毫秒回應**），伺服器沒開才呼叫 API |
| 👯 重複歌曲偵測 | `youtube_toolkit/duplicate_finder.py` | API Key | 以標題「子字串互相包含」比對找出疑似重複的歌，依觀看數排序建議保留哪一首 |
| 🌐 影片關鍵字搜尋 | `youtube_toolkit/video_search.py` | API Key | 呼叫 `search.list` REST API，搜尋近 180 天內的影片（strict 安全搜尋） |
| 🩺 清單健康檢查 | `youtube_toolkit/playlist_health.py` | API Key | 掃描各清單，列出**私人／已刪除／不公開**的影片（網址、位置、可得資訊） |
| ✂️ 失效影片清除 | `youtube_toolkit/playlist_cleaner.py` | OAuth 2.0 | 從清單移除**私人／已刪除**影片（不公開不受影響）；預設 dry-run、留底、二次驗證 |
| 🔌 MCP + REST 伺服器 | `youtube_toolkit/mcp_server.py` | API Key | 把歌單搜尋開放給多個 AI Agent（n8n / Hermes / Claude）與一般 HTTP 服務；記憶體快取、唯讀 |

共用模組：

| 模組 | 說明 |
|------|------|
| `youtube_toolkit/config.py` | **設定中心**：載入 `.env`、提供 API Key／憑證路徑／排程時間等所有設定 |
| `youtube_toolkit/playlists.py` | **播放清單設定載入器**：讀取根目錄 `playlists.toml`，清單名稱→ID 一處管理 |
| `youtube_toolkit/auth.py` | **認證中心**：API Key 服務與 OAuth 2.0 憑證（JSON 快取，不使用 pickle） |
| `youtube_toolkit/youtube_client.py` | **共用資料存取層**：分頁抓清單、批次抓詳情、搬移、搜尋，全部經配額記帳 |
| `youtube_toolkit/sorting.py` | **LIS 搬移計畫**：純函式計算最少搬移次數（可獨立單元測試） |
| `youtube_toolkit/quota_manager.py` | API 配額計數器。軟上限 8,000 / 硬上限 10,000，超過軟上限拋出 `QuotaSoftLimitExceeded` |
| `youtube_toolkit/log_utils.py` | ANSI 彩色 console logger（CRITICAL 紫 / ERROR 紅 / WARNING 黃 / INFO 綠 / DEBUG 青） |

---

## 專案結構

```
youtube_api/
├── youtube_toolkit/            # 主套件（所有程式碼）
│   ├── __init__.py
│   ├── config.py               # 設定中心：.env 載入 + 全部可調參數
│   ├── playlists.py            # playlists.toml 載入器（清單名稱→ID）
│   ├── auth.py                 # 認證中心：API Key / OAuth（JSON 憑證快取）
│   ├── youtube_client.py       # 共用資料存取層（帶配額記帳）
│   ├── sorting.py              # LIS 最少搬移計畫（純函式）
│   ├── song_search.py          # 歌曲比對核心（MCP 伺服器與 CLI 共用）
│   ├── log_utils.py            # 彩色 logging
│   ├── quota_manager.py        # API 配額管理（軟/硬上限）
│   ├── playlist_sorter.py      # 🎯 主力：OAuth 排序 + 每日排程
│   ├── playlist_search.py      # 🔍 歌單載入 + 關鍵字搜尋
│   ├── duplicate_finder.py     # 👯 重複歌曲偵測
│   ├── video_search.py         # 🌐 search.list API 包裝
│   ├── playlist_health.py      # 🩺 清單健康檢查（私人/已刪除/不公開）
│   ├── playlist_cleaner.py     # ✂️ 失效影片清除（dry-run 預設、留底）
│   └── mcp_server.py           # 🔌 MCP + REST 伺服器（快取、唯讀）
├── tests/                      # 單元測試（標準庫 unittest，無額外依賴）
│   ├── test_sorting.py         #    LIS 與搬移計畫的數學正確性
│   ├── test_youtube_client.py  #    分頁、防呆、配額記帳（假 service）
│   ├── test_playlist_sorter.py #    搬移執行、重試、熔斷（假 client）
│   ├── test_quota_manager.py   #    配額持久化、換日歸零、壞檔容錯
│   ├── test_playlists.py       #    playlists.toml 載入與驗證
│   ├── test_duplicate_finder.py#    標題正規化與分組
│   ├── test_log_utils.py       #    上色不污染檔案日誌
│   ├── test_auth.py            #    無人值守模式不開瀏覽器
│   ├── test_playlist_health.py #    私人/已刪除/不公開分類
│   ├── test_playlist_cleaner.py#    候選名單安全性、二次驗證
│   ├── test_playlist_search.py #    快取優先、退回 API、輸入錯誤處理
│   └── test_mcp_server.py      #    快取 TTL 與搜尋邏輯
├── secrets/                    # ⚠️ 機敏憑證（已被 .gitignore 排除）
│   ├── client_secret.json      #    OAuth 用戶端密鑰
│   └── token.json              #    OAuth 憑證快取（自動產生，JSON 格式）
├── docs/
│   └── PROJECT_REPORT.html     # 專案分析報告（架構圖、流程圖、優化建議）
├── docs/MCP_CLIENT_SETUP.md    # 🔌 yt-mcp 客戶端設定指南（n8n / Hermes / REST）
├── Dockerfile                  # yt-mcp 容器映像（uv + Python 3.12）
├── docker-compose.yml          # yt-mcp 服務定義（ai-net 網路、127.0.0.1:8765）
├── playlists.toml              # 📋 播放清單設定：名稱→ID、排序順序、各工具目標
├── quota_state.json            # 配額計數狀態（自動產生，gitignored）
├── sorter_state.json           # 最後排序日期（自動產生，gitignored）
├── logs/                       # 輪替檔案日誌（自動產生，gitignored）
├── .env                        # ⚠️ 機敏設定（已被 .gitignore 排除）
├── .env.example                # .env 範本（可安全入版控）
├── .gitignore
├── playlist_search.spec        # PyInstaller 打包設定
├── pyproject.toml              # 專案設定（PEP 621，由 uv 管理）
├── .python-version             # uv 鎖定的 Python 版本（3.12）
├── uv.lock                     # uv 依賴鎖定檔
├── requirements.txt            # pip 備援依賴（由 uv export 產生）
└── README.md
```

---

## 環境需求與安裝

- [uv](https://docs.astral.sh/uv/)（會自動下載並管理 Python 3.12，無須另行安裝 Python）
- 主要依賴：`google-api-python-client`、`google-auth`、`google-auth-oauthlib`、`schedule`、`requests`

### 使用 uv（推薦）

```bash
uv sync        # 自動建立 .venv、安裝 Python 3.12 與全部依賴
```

### 使用 pip（備援）

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

---

## 憑證與環境設定

所有機敏資訊都放在 **`.env`** 與 **`secrets/`**，兩者皆被 `.gitignore` 排除、不會進版控。程式碼中沒有任何金鑰明碼。

### 第一步：建立 .env

```bash
# 複製範本後填入實際值
copy .env.example .env        # Windows
```

| 環境變數 | 必填 | 預設值 | 說明 |
|----------|:---:|--------|------|
| `YOUTUBE_API_KEY` | ✅ | — | YouTube Data API v3 金鑰 |
| `CLIENT_SECRET_FILE` | | `secrets/client_secret.json` | OAuth 用戶端密鑰路徑 |
| `TOKEN_FILE` | | `secrets/token.json` | OAuth 憑證快取路徑（JSON 格式） |
| `SCHEDULE_TIME` | | `16:05` | 每日排程時間（24 小時制） |
| `YOUTUBE_DAILY_LIMIT` | | `10000` | 配額硬上限 |
| `YOUTUBE_SOFT_LIMIT` | | `8000` | 配額軟上限（熔斷點） |
| `OAUTH_PORT` | | `8080` | OAuth 本機回呼埠號 |
| `MCP_HOST` | | `127.0.0.1` | yt-mcp 綁定位址（容器內設 `0.0.0.0`） |
| `MCP_PORT` | | `8765` | yt-mcp 埠號 |
| `MCP_CACHE_TTL_MINUTES` | | `360` | yt-mcp 清單快取有效時間（分鐘） |
| `MCP_BASE_URL` | | `http://127.0.0.1:8765` | CLI 查詢 yt-mcp 的位址 |
| `LOG_FILE_NAME` | | `youtube_toolkit.log` | 檔案日誌名稱（排序容器用 `sorter.log` 分流） |
| `TZ` | | 系統時區 | 容器內**必須**設為 `Asia/Taipei`，否則排程時間會變成 UTC |

> 讀取優先序：**既有環境變數 > `.env` 檔 > 程式預設值**。

### 第二步（API Key）：唯讀工具用

`playlist_search`、`duplicate_finder`、`video_search` 使用 API Key 讀取公開資料。

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建立專案並啟用 **YouTube Data API v3**
2. 建立 API 金鑰，並在「API 限制」中僅允許 YouTube Data API
3. 填入 `.env` 的 `YOUTUBE_API_KEY`

### 第三步（OAuth 2.0）：排序工具用

`playlist_sorter` 需要**修改**你的播放清單，必須使用 OAuth 2.0（scope: `https://www.googleapis.com/auth/youtube`）。

1. 在 Google Cloud Console 建立 **OAuth 用戶端 ID**（應用程式類型：電腦版應用程式）
2. 下載 JSON 並存為 `secrets/client_secret.json`
3. 首次執行時會自動開啟瀏覽器要求授權（本機 `port 8080` 回呼）
4. 授權成功後憑證會以 **JSON 格式**快取到 `secrets/token.json`，之後自動載入／刷新；Refresh Token 失效時會自動重新走授權流程
> 憑證自 v0.3.0 起改用 JSON（舊的 pickle 格式已停用，殘檔已於 2026-07-30 清除）。

---

## 使用方式

**目標清單與排序集合統一設定於 [`playlists.toml`](playlists.toml)**——換工具的目標清單改對應區段的
`target` 字串、調整每日排序的集合／順序改 `[sorter].order`，不用動任何程式碼。

所有工具都以 **模組方式** 從專案根目錄執行（或先 `uv sync` 後使用對應的指令）：

| 工具 | 直接執行 | uv 指令 |
|------|----------|---------|
| 播放清單自動排序 | `uv run python -m youtube_toolkit.playlist_sorter` | `uv run yt-sort` |
| 歌單關鍵字搜尋 | `uv run python -m youtube_toolkit.playlist_search <關鍵字>` | `uv run yt-playlist-search <關鍵字>` |
| 重複歌曲偵測 | `uv run python -m youtube_toolkit.duplicate_finder` | `uv run yt-duplicates` |
| 影片關鍵字搜尋 | `uv run python -m youtube_toolkit.video_search` | `uv run yt-video-search` |
| 清單健康檢查 | `uv run python -m youtube_toolkit.playlist_health` | `uv run yt-health` |
| 失效影片清除 | `uv run python -m youtube_toolkit.playlist_cleaner` | `uv run yt-clean` |
| MCP + REST 伺服器 | `uv run python -m youtube_toolkit.mcp_server` | `uv run yt-mcp`（容器：`docker compose up -d`） |

### 1. 播放清單自動排序（主力工具）

**建議以容器常駐執行**（`docker compose up -d`，見下方「常駐服務」一節）：Docker Desktop
開機自啟 ＋ `restart: unless-stopped`，重開機後自動復活，不必記得手動啟動。

執行模式：

| 指令 | 行為 |
|------|------|
| `docker compose up -d yt-sorter` | **推薦**：容器常駐，每天 16:05 自動排序，開機自動復活 |
| `uv run yt-sort` | 主機常駐：立即執行一輪後待命，每天 16:05 再執行（`Ctrl+C` 結束） |
| `uv run yt-sort --once` | 只執行一輪就結束（也用於**手動重新 OAuth 授權**） |
| `uv run yt-sort --dry-run` | 只顯示每份清單的 LIS 搬移計畫與預估配額成本，**不寫入 YouTube** |
| `uv run yt-sort --unattended` | 無人值守：憑證失效時記錯誤而非開瀏覽器（容器預設帶此旗標） |

> **啟動補跑機制**：常駐程序啟動時會立即跑一輪，作為「機器關機期間漏跑」的補償；
> 但同一天內重複啟動不再重跑（記錄於 `sorter_state.json`），因此重開機或容器重建
> 不會白燒配額。每天 16:05 的排程不受此限制。

> **無人值守保護**：排程觸發的執行若遇到憑證失效，**不會**開瀏覽器等授權（那會讓
> daemon 卡死），改記 ERROR 並於次日再試；請在主機執行 `uv run yt-sort --once` 完成重新授權。

每次作業依序處理 `playlists.toml` 中 `[sorter].order` 設定的清單（建議**由小到大排列**，確保配額耗盡前小清單能全部完成），流程為：

```
認證（13 份清單共用一次）→ 分頁抓取清單項目 → 批次抓取影片詳情（50 部/批）
     → 本地排序（頻道 A→Z、觀看數高→低）
     → LIS 搬移計畫：找出「相對順序已正確的最大子集」原地不動，
        只搬其餘項目 → 搬移次數 = n - len(LIS)，為數學上的最少值
     → 逐步執行搬移（每步 50 quota，暫時性錯誤最多重試 5 次、指數退避）
     → 累計成本觸及軟上限 8,000 → 安全中止整個作業，保留剩餘配額
```

> 手動模式：`PlaylistSorter(playlist_id, client=YouTubeClient.for_authorized_user())` 後呼叫 `run(auto_run=False)`，會先顯示 LIS 計算出的精確搬移數與預估配額，要求輸入 `yes` 確認才寫入。

### 2. 歌單關鍵字搜尋

```bash
uv run yt-playlist-search CAPPER              # 搜尋預設清單（playlists.toml 的 target）
uv run yt-playlist-search CAPPER --all        # 搜尋全部 13 份清單
uv run yt-playlist-search CAPPER -p Japanese  # 指定清單
uv run yt-playlist-search CAPPER -n 10        # 最多顯示 10 筆
uv run yt-playlist-search --dump              # 印出整份清單（不搜尋）
uv run yt-playlist-search CAPPER --no-server  # 略過快取，直接呼叫 YouTube API
uv run yt-playlist-search CAPPER -v           # 顯示逐筆配額等除錯訊息
```

比對歌名與頻道名稱、不分大小寫，關鍵字需 ≥ 2 個字元。輸出範例：

```
🔍「40mP」在 YTMusic 共 2 首　（yt-mcp 快取，0 units）

  1. 【40meterP】40mP & miri「レイラ」
     https://youtu.be/gqWAui-okAM　398,108 觀看　YTMusic 第 7 首
```

**查詢優先走 yt-mcp 伺服器的記憶體快取**（`docker compose up -d` 後即生效）：
毫秒回應、**不消耗任何配額**；伺服器沒開時自動退回直接呼叫 API（會重新載入整份清單，
YTMusic 約 42 units）。兩條路徑共用 `song_search.py` 的比對邏輯，結果保證一致。

> 程式化使用（例如聊天機器人）可改用 `YouTubeAPIHandler.search_keyword_in_song_list()`，
> 它會把結果切成 ≤1,900 字元的分段字串（為 Discord 2,000 字元上限預留）。

### 3. 重複歌曲偵測

輸出範例：

```
=== 發現 N 組疑似重複的歌曲 ===
【第 1 組】
  👑 保留? [12,345,678 觀看] 周杰倫 Jay Chou【七里香】Official MV
  ❌ 刪除? [456,789 觀看] 七里香
```

比對邏輯：標題先**正規化**——小寫、括號符號移除（**內容保留**，「【七里香】」的歌名還在）、去除
Official MV／Lyric Video／官方完整版／feat. 等宣傳雜訊、壓縮空白——之後互為子字串即視為同組
（`live`／`cover` 等有語意的詞不移除，現場版不會和原版誤判成重複）。每組依觀看數由高到低排序，
第一首建議保留。**僅產生報告，不會自動刪除任何影片。**

### 4. 影片關鍵字搜尋

搜尋近 180 天內的影片，可調整 `results_count`（預設 50）與 `search_order`（relevance / date / rating / title / viewCount）。注意每次呼叫消耗 **100 units**。

### 5. 清單健康檢查

```bash
uv run yt-health              # 掃描 playlists.toml 的全部清單
uv run yt-health YTMusic      # 只掃描指定清單（可多個）
```

偵測原理：`playlistItems.list` 回傳清單裡**所有**項目（含壞掉的），`videos.list` 只回傳
還存在的影片——兩邊對照，缺席者即私人或已刪除；不公開（unlisted）由影片的
`status.privacyStatus` 判讀。輸出範例：

```
⚠️ BGM / OST：268 部影片中發現 6 個問題項目
  🔒 私人（2）：
    - 第  160 首  https://youtu.be/DbF9RQphIss  Private video
  🗑️ 已刪除（4）：
    - 第  156 首  https://youtu.be/yYrLMsd3XAU  Deleted video
```

> 私人／已刪除影片的**原始標題已被 YouTube 抹除**，能列出的是網址、清單位置與 videoId；
> 不公開影片仍可播放（標題、頻道完整可見），但屬於高下架風險族群。
> 無法讀取的私人清單會自動跳過並註記。掃描約 2,500 部影片 ≈ 115 units。

### 6. 失效影片清除

```bash
uv run yt-clean                  # dry-run：只列出將移除的名單（不動任何東西）
uv run yt-clean --apply          # 實際移除（重新驗證 → 留底 → 輸入 yes 確認）
uv run yt-clean --apply --yes    # 跳過互動確認（非互動情境用）
```

只移除**私人／已刪除**的清單項目（`playlistItems.delete`，每筆 50 units），
**不公開影片永遠不會被移除**（程式硬性排除＋單元測試把關）。四道防線：
預設 dry-run → 刪除前重新向 API 驗證（讀得到詳情者一律剔除）→ 完整名單留底至
`logs/cleanup-*.txt` → 互動輸入 `yes` 確認。只影響播放清單，不影響影片本身。

### 7. 常駐服務（docker compose）

兩個服務共用同一個映像與同一份專案掛載，因此**配額計數也是合併的**：

| 服務 | 容器名 | 用途 |
|------|--------|------|
| `yt-sorter` | `yt-playlist-sorter` | 每日 16:05 自動排序（取代主機常駐視窗） |
| `yt-music-mcp` | `yt-music-mcp` | 歌單搜尋 MCP + REST 伺服器（`127.0.0.1:8765`） |

```bash
docker compose up -d --build    # 建置並啟動兩個服務
docker compose ps               # 看狀態（yt-music-mcp 有健康檢查）
docker compose logs -f          # 看日誌
docker compose restart          # 改程式碼／playlists.toml 後重啟即生效
```

> 專案目錄是掛載進容器的，且套件以 editable 方式安裝，因此**改程式碼只需 restart**，
> 只有改依賴才需要 `--build`。容器內 `TZ=Asia/Taipei` 是必要設定——
> `schedule` 用本地時間，不設會讓 16:05 變成 UTC 16:05（台灣半夜 00:05）。

#### MCP + REST 搜尋伺服器

把歌單搜尋開放給多個 AI Agent（n8n、Hermes、Claude）與一般 HTTP 服務同時查詢：

```bash
docker compose up -d yt-music-mcp   # 容器長駐（建議）
uv run yt-mcp                       # 或本機直接跑
```

- **MCP 端點**（Streamable HTTP）：`http://127.0.0.1:8765/mcp`，工具：`search_songs`／`list_playlists`／`refresh_playlist`
- **REST 端點**：`GET /search?q=...&playlist=...`、`/playlists`、`/refresh`、`/health`
- 清單載入一次後**常駐記憶體快取**（TTL 6 小時），查詢不耗 YouTube 配額；抓取經 QuotaManager 與其他工具合併記帳
- **唯讀**：不暴露任何寫入功能；埠只映射到宿主機 `127.0.0.1`，容器間走 `ai-net` 網路

> 客戶端（n8n MCP Client Tool 節點、Hermes、curl）完整設定見 **[docs/MCP_CLIENT_SETUP.md](docs/MCP_CLIENT_SETUP.md)**。

---

## API Quota 成本與保護機制

YouTube Data API 免費配額為 **每日 10,000 units**（太平洋時間午夜重置），各操作成本差異極大：

| API 操作 | 成本（units） | 使用場景 |
|----------|:---:|----------|
| `playlistItems.list` | 1 | 抓取清單內容（每頁 50 筆） |
| `videos.list` | 1 | 批次抓影片詳情（每批 50 部） |
| `playlistItems.update` | **50** | 搬移一首歌的位置 |
| `search.list` | **100** | 關鍵字搜尋 |

`QuotaManager` 的保護策略：

- **所有** API 呼叫（含搜尋）都經過共用的 `YouTubeClient`，呼叫前先 `consume(cost)` 預扣並檢查
- 計數**持久化**於 `quota_state.json`：同一配額日（太平洋時間，即 YouTube 的重置基準）內重啟程式**不歸零**，且所有工具共用同一份計數、合併記帳
- 累計即將超過**軟上限 8,000** → 拋出 `QuotaSoftLimitExceeded`，中止本日整個排序作業（保留 2,000 units 緩衝給其他用途）
- API 回傳 `quotaExceeded`（硬上限）→ 立即停止，明日再試
- 排序採 **LIS 最少搬移計畫**：相對順序已正確的歌原地不動，**一首都不用搬時整份清單只花「讀取」的個位數 units**

> 💡 換算：軟上限 8,000 units ≈ 每天最多搬移約 **160 首**歌的位置。千首等級的大清單第一次排序需要多天才能收斂，之後每日維護成本極低。

---

## 開發與測試

單元測試使用標準庫 `unittest`（無額外依賴），覆蓋 LIS 搬移計畫的數學正確性、
分頁／防呆邏輯、配額記帳與持久化、排序器重試／熔斷、標題正規化與日誌格式
（以假 service／假 client 隔離網路）：

```bash
uv run python -m unittest discover -s tests -v
```

---

## 打包為 Windows 執行檔

使用 PyInstaller 將歌單搜尋工具打包成單一執行檔：

```bash
uv run --with pyinstaller pyinstaller playlist_search.spec
# 產物：dist/playlist_search.exe
```

> 部署時請將 `.env` 與 `secrets/` 目錄放在 exe **旁邊**（打包後程式會以執行檔所在目錄為根目錄尋找設定）。
> 舊的 `dist/main.exe` 為重構前（2024-02）的產物，已過時。

---

## 安全性注意事項

1. **`.env`、`secrets/client_secret.json`、`secrets/token.json` 為機敏檔案**，擁有它們等於能使用你的配額、操作你的 YouTube 帳號播放清單。三者皆已列入 `.gitignore`，請勿以任何形式上傳或分享。
2. **API Key 曾以明碼存在於舊版原始碼中**，建議至 Google Cloud Console 輪替（Rotate）產生新金鑰後更新 `.env`，並對金鑰設定「API 限制」（僅允許 YouTube Data API）。
3. OAuth 憑證快取自 v0.3.0 起改用 **JSON 格式**（google-auth 官方作法），不再使用 pickle，消除反序列化執行任意程式碼的風險。
