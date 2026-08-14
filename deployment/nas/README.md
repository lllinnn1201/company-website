# NAS deployment

This project runs both the Flask website and MySQL in Synology Container Manager. MySQL and the web app are bound only to NAS localhost; the web app is ready for DSM Reverse Proxy. The app uses the NAS network rather than Docker bridge networking because this NAS's Docker bridge DNS cannot resolve external hosts.

## Deploy

1. Copy this repository to a persistent NAS folder, such as `/volume1/docker/company-website`.
2. In `deployment/nas`, copy `.env.example` to `.env`. Replace every placeholder with a different long random value.
3. In Container Manager, choose **Project** → **Create** → **Create project from existing compose file**, then select `deployment/nas/compose.yaml`.
4. Start the project. Wait until `company-portal-mysql` and `company-portal-app` both report healthy.
5. In DSM **Control Panel** → **Login Portal** → **Reverse Proxy**, add an HTTPS source for your domain and set its destination to `http://localhost:8000`.

Do not create router port forwarding for 3307 or 8000. Only publish your HTTPS website through DSM's reverse proxy.

On its first start only, MySQL loads `database/schema.sql` and `database/seed.sql`, which are copied into its image during build to avoid Synology shared-folder permission issues. They do not overwrite an existing MySQL volume.

## Scheduled data updates

Make the task scripts executable once:

```sh
chmod +x /volume1/docker/company-website/deployment/nas/backup.sh
chmod +x /volume1/docker/company-website/deployment/nas/sync.sh
```

Create two DSM **Task Scheduler** user-defined scripts:

```sh
# Every day, for example 09:00
/volume1/docker/company-website/deployment/nas/sync.sh news

# On the 5th day of every month, for example 10:00
/volume1/docker/company-website/deployment/nas/sync.sh cci
```

The scripts call the API internally and keep `CRON_SECRET` inside the container; it is never sent from a public client.

## Backups

Create one more daily DSM Task Scheduler script:

```sh
/volume1/docker/company-website/deployment/nas/backup.sh /volume1/backups/company-portal
```

It retains 30 days of compressed SQL backups. Use Hyper Backup (or equivalent) to copy `/volume1/backups/company-portal` to a separate device or cloud storage location.

## Restore

Stop the web application first. After checking the exact backup file and target database, run:

```sh
gunzip -c /volume1/backups/company-portal/ai_news_YYYY-MM-DD_HH-MM-SS.sql.gz | docker exec -i company-portal-mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
```

This writes into the active database.
