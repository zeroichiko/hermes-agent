# 與其他 ChatGPT 自動化工具的比較

## 搜尋結果摘要

GitHub 上以 "chatgpt cdp python automation" 等關鍵字搜尋，同類工具極少。
只有 3 個有相關性的專案：

| 專案 | Stars | 語言 | 連線方式 |
|------|-------|------|----------|
| soberized/ChatGPT-API-Bypass | 17 | Python | Selenium (undetected_chromedriver) |
| sametcodes/gpteer | 10 | TypeScript | Node.js headless browser |
| coinpost/sourcefinder-tool | 0 | Go | Chrome DevTools Protocol |

## 與 soberized/ChatGPT-API-Bypass 的關鍵差異

這是唯一功能相近的開放原始碼工具（Python，不需要 API Key）。

### 技術路線不同

| 維度 | chatgpt_cdp.py | ChatGPT-API-Bypass |
|------|---------------|-------------------|
| **瀏覽器控制** | 原生 CDP WebSocket | Selenium + undetected_chromedriver |
| **Chrome 執行** | 操控已有的 Chrome 進程（port 9222） | 每次新建獨立 headless Chrome |
| **打字方式** | `Input.dispatchKeyEvent` 逐字元 | `element.innerHTML =` 再發 `input` 事件 |
| **回答偵測** | `--check N` 智慧輪詢 body length 成長 | 等 `Stop generating` 按鈕出現再消失（硬等 180 秒） |
| **登入窗處理** | 4 層遞進偵測 + 模擬點擊 + fallback | 用 Selenium wait 等待輸入框可見 |
| **截圖** | 內建 `Page.captureScreenshot` | 無 |
| **多輪/新對話** | `--new` 自動導航到 /new | 每次執行重新開啟 Chrome |
| **事件排空** | 每次操作前 `drain_events()` 防止事件淹沒 | 無 |
| **React textarea** | 已知問題並正確處理（Input domain） | 用 innerHTML + event dispatch（React 可能不觸發） |

### ChatGPT-API-Bypass 的已知問題

1. **每次重啟 Chrome** — 效能差，不共享你的 Chrome 環境（cookies、分頁、設定）
2. **innerHTML 打字** — ChatGPT 是 React SPA，innerHTML 不會觸發任何事件監聽器，可能無法送出訊息
3. **硬等 180 秒** — 短回答也要等那麼久，長回答可能不夠
4. **沒有事件排空** — SPA 頁面事件可能淹沒後續指令
5. **Selenium 中繼層** — 多一層抽象，出錯難除錯

### 我們的獨特優勢（經過驗證）

- 原生 CDP WebSocket：直接連 `ws://127.0.0.1:9222/devtools/page/xxx`
- 操控已有 Chrome：共享你的 Chrome 環境
- 智慧輪詢 `--check N`：不等固定秒數，偵測 body length 成長直到穩定 2 次
- 完整登入窗處理：4 層遞進 + fallback
- 截圖/HTML 抓取內建
- `--new` 自動新對話

## 與其他工具的比較

### kardolus/chatgpt-cli (921⭐, Go)
- 官方 API 封裝，需 API Key
- Agent 模式、MCP 支援、多模型
- 完全不同的生態系（API vs 瀏覽器自動化）

### firtoz/GPT-Shell (Node.js)
- Discord Bot，需 MongoDB + Bot Token
- 長程記憶（Pinecone）、自訂提示詞、Wolfram Alpha
- 完全不同的生態系（Discord 機器人 vs CLI 工具）

### 0xacx/chatgpt-shell-cli (Shell script)
- 一行 curl 安裝，需 API Key
- DALL-E 圖片、pipe 模式、command 生成器
- 輕便但需要 API Key

### coinpost/sourcefinder-tool (Go, CDP)
- 用 CDP 但用途不同：批次處理多輸入 → 多 AI 服務
- 支援 ChatGPT/Grok 等多服務
- 定位：fact-check 工具，非個人對話自動化

## 結論

chatgpt_cdp.py 是唯一同時具備以下條件的開放原始碼工具：
1. 原生 CDP WebSocket（不是 Selenium）
2. 操控已有 Chrome（不重啟）
3. 零 API Key（未登入模式）
4. 智慧輪詢回答（不等固定秒數）
5. 完整登入窗自動處理
6. 截圖/HTML 抓取內建
