FROM mysql:8.4

# Synology shared-folder ACLs can make bind-mounted SQL unreadable by the
# mysql user. Copying the initial schema into the image avoids that issue.
COPY database/schema.sql /docker-entrypoint-initdb.d/01-schema.sql
COPY database/seed.sql /docker-entrypoint-initdb.d/02-seed.sql
RUN chmod 644 /docker-entrypoint-initdb.d/01-schema.sql /docker-entrypoint-initdb.d/02-seed.sql
