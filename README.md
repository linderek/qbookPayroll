# QuickBooks Online Sandbox 自動化工具

本機開發用的 Flask 小工具，透過 Intuit OAuth 2.0 連接 **QuickBooks Online Sandbox**，
可讀取公司資訊、Vendor、Expense Account，並建立測試用的 Sandbox Bill。

**這個工具只支援 Sandbox，不會、也不能呼叫 Production API。**

## 功能

- 首頁顯示是否已連接 QuickBooks（讀取本機 `token.json`）
- 「Connect to QuickBooks」→ Intuit OAuth 2.0 authorization code flow（含 CSRF state 驗證）
- OAuth callback 換取 access token / refresh token，存於本機 `token.json`（僅供開發階段使用）
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
token_store.py          # token.json 讀寫與 token 刷新
requirements.txt
.env.example
.gitignore
templates/
  index.html
  bill_form.html
  result.html
```

`token.json` 會在完成 OAuth 授權後自動產生於專案根目錄，`.env` 與 `token.json` 皆已加入
`.gitignore`，不會被提交到版本控制。

## 前置準備

1. 前往 [developer.intuit.com](https://developer.intuit.com/)，建立一個 App，並啟用
   **Accounting** scope。
2. 在該 App 的 **Keys & OAuth** 設定中，取得 **Sandbox** 區塊的 Client ID / Client Secret
   （注意：不是 Production 的 Keys）。
3. 在同一個設定頁面的 Redirect URIs（Sandbox）加入：
   ```
   http://localhost:8000/callback
   ```
4. 在 App 的 Sandbox 分頁確認已有一個 Sandbox 測試公司（Intuit 會自動建立一個）。

## 安裝與設定

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

編輯 `.env`：

```
QBO_CLIENT_ID=<你的 Sandbox Client ID>
QBO_CLIENT_SECRET=<你的 Sandbox Client Secret>
QBO_REDIRECT_URI=http://localhost:8000/callback
QBO_ENVIRONMENT=sandbox
FLASK_SECRET_KEY=<隨機字串，可用下方指令產生>
```

產生 `FLASK_SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 執行

```bash
python app.py
```

開啟 <http://localhost:8000>。

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
8. 手動修改 `token.json` 的 `access_token_expires_at` 為過去時間，重新點擊任一功能，確認會自動
   刷新 access token（觀察 console log 只會顯示遮罩後的訊息，不會印出完整 token 或 client secret）。
9. 刪除或清空 `token.json`，確認回到「尚未連接」狀態，需重新走一次 OAuth 流程。

## 安全性注意事項

- Client ID、Client Secret、access token、refresh token 一律不寫死在程式碼中，只透過 `.env` 與
  `token.json` 讀取。
- Log 內容不會輸出 Client Secret、access token 或 refresh token 的實際內容。
- 所有對 QuickBooks API 的請求都會設定 timeout，並依 400 / 401 / 403 / 429 / 500 分別處理錯誤訊息。
- API base URL 為程式內寫死的 Sandbox 常數，且啟動時會檢查 `QBO_ENVIRONMENT` 必須為 `sandbox`，
  避免誤連 Production 環境。
- 本工具僅供單一開發者本機使用，`debug=True` 僅適用於本機開發，請勿對外公開此服務。
