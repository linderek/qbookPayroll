# QuickBooks Online Sandbox 自動化工具

Flask 小工具，透過 Intuit OAuth 2.0 連接 **QuickBooks Online Sandbox**，
可讀取公司資訊、Vendor、Expense Account，並建立測試用的 Sandbox Bill。
可在本機執行，也可以部署到 Vercel。

**這個工具只支援 Sandbox，不會、也不能呼叫 Production API。**

## 功能

- 首頁顯示是否已連接 QuickBooks
- 「Connect to QuickBooks」→ Intuit OAuth 2.0 authorization code flow（含 CSRF state 驗證）
- OAuth callback 換取 access token / refresh token，存於 Upstash Redis
- access token 到期自動刷新（access token 過期前主動刷新；若仍收到 401 則刷新後重試一次）
- 讀取 CompanyInfo
- 列出 Vendor
- 列出 Expense Account
- 建立 Sandbox Bill（僅能挑選既有 Vendor 與既有 Expense Account，不會建立新的 Vendor 或 Account；
  金額預設 10；必須勾選「我確認這是 Sandbox 測試資料」才能送出）

## 檔案結構

```
app.py                  # Flask 路由與 OAuth flow
quickbooks_client.py    # QuickBooks Sandbox API 呼叫與錯誤處理
token_store.py          # token 讀寫（Upstash Redis）與 token 刷新
vercel.json             # Vercel 部署設定
requirements.txt
.env.example
.gitignore
templates/
  index.html
  bill_form.html
  result.html
```

## 為什麼用 Upstash Redis 存 token，而不是本機檔案

Vercel 這類 serverless 平台的檔案系統除了 `/tmp` 外唯讀，而 `/tmp` 只存在於單次呼叫的執行環境中，
不同請求可能落在不同的執行個體，本機檔案（如 `token.json`）無法在請求之間可靠地保存。因此 token
改存在 Upstash Redis（透過 REST API 存取，不需要長連線，跟 serverless 相容），本機開發與 Vercel
部署共用同一套程式碼。

## 前置準備

### 1. Intuit 開發者 App

1. 前往 [developer.intuit.com](https://developer.intuit.com/)，建立一個 App，並啟用
   **Accounting** scope。
2. 在該 App 的 **Keys & OAuth** 設定中，取得 **Sandbox** 區塊的 Client ID / Client Secret
   （注意：不是 Production 的 Keys）。
3. 在同一個設定頁面的 Redirect URIs（Sandbox）加入你會用到的網址：
   - 本機開發：`http://localhost:8000/callback`
   - Vercel 部署：`https://<你的專案>.vercel.app/callback`（部署後才會知道實際網址，屆時回來補上）
4. 在 App 的 Sandbox 分頁確認已有一個 Sandbox 測試公司（Intuit 會自動建立一個）。

### 2. Upstash Redis（免費額度即可）

1. 前往 [upstash.com](https://upstash.com/) 註冊，建立一個 Redis database。
2. 在該 database 的 Details 頁面複製 **REST URL** 與 **REST TOKEN**。

## 本機安裝與設定

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

編輯 `.env`：

```
INTUIT_CLIENT_ID=<你的 Sandbox Client ID>
INTUIT_CLIENT_SECRET=<你的 Sandbox Client Secret>
INTUIT_REDIRECT_URI=http://localhost:8000/callback
QUICKBOOKS_ENVIRONMENT=sandbox
FLASK_SECRET_KEY=<隨機字串，可用下方指令產生>
UPSTASH_REDIS_REST_URL=<你的 Upstash REST URL>
UPSTASH_REDIS_REST_TOKEN=<你的 Upstash REST TOKEN>
```

產生 `FLASK_SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 執行

```bash
python app.py
```

開啟 <http://localhost:8000>。

## 部署到 Vercel

1. 確認專案已推送到 GitHub（`vercel.json`、程式碼都在版本控制內；`.env` 不會被推送，這是正常的）。
2. 到 [vercel.com](https://vercel.com/) 用該 GitHub repo 建立新專案，Framework Preset 選
   **Other**（`vercel.json` 已經指定用 `@vercel/python` 建置 `app.py`）。
3. 在 Vercel 專案的 **Settings → Environment Variables** 加入以下變數（值不要寫進程式碼或提交到
   git，只在這裡設定）：
   ```
   INTUIT_CLIENT_ID
   INTUIT_CLIENT_SECRET
   INTUIT_REDIRECT_URI       = https://<你的專案>.vercel.app/callback
   QUICKBOOKS_ENVIRONMENT    = sandbox
   FLASK_SECRET_KEY
   UPSTASH_REDIS_REST_URL
   UPSTASH_REDIS_REST_TOKEN
   ```
   第一次部署前網址還不確定，可以先隨便填一個，等 Vercel 分配好網域後，回來把
   `INTUIT_REDIRECT_URI` 更新成實際網址，並同步更新到 Intuit App 的 Redirect URIs。
4. 觸發部署（`git push` 或在 Vercel 後台按 Deploy）。
5. 部署完成後，回到 Intuit 開發者後台的 Keys & OAuth，把 Sandbox Redirect URIs 加上你實際的
   `https://<你的專案>.vercel.app/callback`。
6. 開啟 `https://<你的專案>.vercel.app`，走一次「連接 → 讀取資料 → 建立 Bill」確認正常運作。

**注意**：`QUICKBOOKS_ENVIRONMENT` 必須維持 `sandbox`，這是程式啟動時的強制檢查，不論部署在哪裡都
不會、也不能連到 Production API。

## 手動測試順序

1. 開啟首頁，確認顯示「尚未連接 QuickBooks」。
2. 點擊「Connect to QuickBooks」，確認導向 Intuit 登入／授權頁面。
3. 選擇 Sandbox 測試公司並同意授權，確認導回首頁並顯示「已連接」與 Realm ID。
4. 點擊「讀取 CompanyInfo」，確認顯示的公司名稱與 Sandbox 公司相符。
5. 點擊「列出 Vendor」，確認顯示 Sandbox 內建的範例廠商。
6. 點擊「列出 Expense Account」，確認顯示 Sandbox 內建的費用科目。
7. 點擊「建立 Sandbox Bill」：
   - 確認廠商與費用科目下拉選單是即時從 QuickBooks 取得（非寫死）。
   - 確認金額欄位預設為 10。
   - 不勾選確認框時，確認「建立 Bill」按鈕維持停用；即使繞過前端直接送出，伺服器端也會拒絕並提示錯誤。
   - 勾選確認框、送出後，確認顯示建立成功的 Bill 內容，並可在 QuickBooks Sandbox 網頁介面
     （qbo.intuit.com）核對該筆 Bill。
8. 到 Upstash 後台的 Data Browser 手動修改儲存的 token JSON 中 `access_token_expires_at` 為過去時
   間，重新點擊任一功能，確認會自動刷新 access token（觀察 console log 只會顯示遮罩後的訊息，不會
   印出完整 token 或 client secret）。
9. 在 Upstash 後台刪除該筆 key，確認回到「尚未連接」狀態，需重新走一次 OAuth 流程。

## 安全性注意事項

- Client ID、Client Secret、access token、refresh token 一律不寫死在程式碼中，只透過環境變數
  （`.env` 本機 / Vercel Environment Variables 雲端）與 Upstash Redis 讀取。
- Log 內容不會輸出 Client Secret、access token 或 refresh token 的實際內容。
- 所有對 QuickBooks API 的請求都會設定 timeout，並依 400 / 401 / 403 / 429 / 500 分別處理錯誤訊息。
- API base URL 為程式內寫死的 Sandbox 常數，且啟動時會檢查 `QUICKBOOKS_ENVIRONMENT` 必須為
  `sandbox`，避免誤連 Production 環境。
- `debug=True` 僅在本機以 `python app.py` 直接執行時生效；Vercel 是透過 WSGI 呼叫 `app` 物件，不會
  進入這個區塊。
