# MySQL（Navicat 本機版）

1. 在 Navicat 連線到本機 MySQL（通常主機為 `127.0.0.1`、埠號為 `3306`）。
2. 右鍵連線後選擇「新增查詢」，開啟 `schema.sql`，按執行；它會建立 `ai_news` 資料庫與所有資料表。
3. 開啟並執行 `seed.sql`，建立首頁分類、指數系列與 Hero 預設文案。
4. 建立應用程式帳號（密碼請自行替換）：

```sql
CREATE USER IF NOT EXISTS 'portal_app'@'localhost' IDENTIFIED BY '請替換成強密碼';
CREATE USER IF NOT EXISTS 'portal_app'@'127.0.0.1' IDENTIFIED BY '請替換成同一組強密碼';
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_news.* TO 'portal_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_news.* TO 'portal_app'@'127.0.0.1';
FLUSH PRIVILEGES;
```

5. 將 `.env.example` 複製為 `.env`，填入相同帳號密碼。`.env` 已被 Git 忽略。

## 每日新聞同步

部署到 Vercel 後，`vercel.json` 會在每天 **台北時間 09:00**（UTC 01:00）呼叫
`/api/sync/news`。它會同步並寫入 MySQL 的三個分類：不動產、建材／原物料、黃仁勳 AI
趨勢；首頁只讀取 MySQL，因此使用者重新整理頁面不會再觸發爬蟲。

在 Vercel 專案的 Environment Variables 設定 `CRON_SECRET`，值需與 `.env` 相同。Vercel
Cron 會以 `Authorization: Bearer <CRON_SECRET>` 保護呼叫。若要手動測試，可執行：

```bash
curl -X POST http://127.0.0.1:5000/api/sync/news -H "Authorization: Bearer <CRON_SECRET>"
```

## 行政院主計總處 CCI

匯入 `schema.sql` 與 `seed.sql` 後，執行下列指令即可將行政院主計總處統計資料庫的營造工程總指數、水泥、鋼筋及預拌混凝土（月資料、民國 110 年＝100）寫入 `index_observations`：

```bash
curl -X POST http://127.0.0.1:5000/api/sync/cci -H "Authorization: Bearer <CRON_SECRET>"
```

系統每月 5 日 UTC 02:00 自動同步；每次同步會更新官方可能修正過的月份，首頁僅顯示最近 12 個可用月份。資料來源為 [行政院主計總處統計資料庫](https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?funid=queryXls&sys=210)。

首次上線前請再執行一次 `seed.sql`，以確保三個分類皆已建立。同步資料以既有的文章來源欄位
標記，因此不需變更資料表權限；每次只替換由 RSS 建立的文章，不會刪除手動建立的文章。

目前 schema 完整承接首頁資料：文章、影片、分類、標籤、文章段落／分析內容，以及建材指數。Flask 已可從資料表讀取；文章或指數資料尚未匯入時，首頁會暫時使用既有展示資料。
