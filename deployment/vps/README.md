# 雲端 VPS 部署

此設定會在一台 Ubuntu VPS 上啟動 Flask、私有 MySQL 與 Caddy。只有 Caddy 對外開放 80/443；MySQL 和 Flask 不會直接暴露到網際網路。Caddy 在網域 DNS 指到 VPS 後會自動申請與續期 HTTPS 憑證。

## 事前準備

1. 準備一台 Ubuntu 22.04 或 24.04 VPS（2 GB RAM 起），並取得可 `sudo` 的 SSH 帳號與固定公網 IP。
2. 在 DNS 新增 `portal.example.com` 的 A 紀錄，指向該 IP。將下列範例中的網域替換為實際網域。
3. 在雲端防火牆及 VPS 防火牆只開放 TCP 80、443 與 SSH 22。不要開放 3306 或 8000。

## 第一次部署

在 VPS 執行：

```sh
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker "$USER"
# 重新登入 SSH 後繼續
git clone <你的儲存庫網址> company-website
cd company-website/deployment/vps
cp .env.example .env
```

編輯 `.env`：設定實際 `DOMAIN`，並以 `openssl rand -base64 32` 產生三個不同的密碼／密鑰。完成後啟動：

```sh
docker compose up -d --build
docker compose ps
```

首次 MySQL 初始化完成後，開啟 `https://portal.example.com`。Caddy 會自動設定 HTTPS；若憑證申請失敗，先確認 DNS 已傳播且 80/443 可從外網連線。

## 更新與備份

```sh
cd ~/company-website
git pull
cd deployment/vps
docker compose up -d --build
docker compose exec -T mysql sh -c 'exec mysqldump --single-transaction -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' | gzip > "ai_news_$(date +%F).sql.gz"
```

將最後一個備份指令設為每天執行，並將備份複製至另一個儲存位置。資料庫 volume 首次建立時會載入 `database/schema.sql` 與 `database/seed.sql`；之後重新部署不會覆寫資料。

## 定時同步資料

每天以 VPS 的 cron 或 systemd timer 執行以下命令。它在容器內部帶入密鑰，密鑰不會出現在網址或瀏覽器：

```sh
docker compose exec -T app python -c 'import os; from urllib.request import Request,urlopen; r=Request("http://127.0.0.1:8000/api/sync/news",data=b"",method="POST",headers={"Authorization":"Bearer "+os.environ["CRON_SECRET"]}); print(urlopen(r,timeout=120).read().decode())'
```

建議：若內容僅供同事使用，請在 DNS/反向代理前加上公司 VPN、Cloudflare Access 或其他 SSO；目前網站本身沒有登入機制。
