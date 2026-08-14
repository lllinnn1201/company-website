"""MySQL access helpers for the portal.

All reads are optional: when .env has not been configured yet, the Flask app
continues to serve its existing in-memory content.
"""

import json
import os
from datetime import timezone

try:
    import pymysql
    from dotenv import load_dotenv
    from pymysql.cursors import DictCursor
    load_dotenv()
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False


def _connection_settings():
    """Return local MySQL settings only when every required value is present."""
    if not HAS_PYMYSQL:
        return None
    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    if not all(os.getenv(key) for key in required):
        return None

    return {
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }


def database_is_configured():
    """Whether this process has everything needed to read and write MySQL."""
    return bool(_connection_settings())


def replace_cci_observations(source, source_url, observations):
    """Upsert one official CCI download and retain an audit record of the run.

    ``observations`` is a mapping of our stable series slug to monthly values.
    A monthly observation is never deleted, so a later official revision replaces
    the value for that month rather than creating a duplicate.
    """
    settings = _connection_settings()
    if not settings:
        raise RuntimeError("MySQL is not configured.")
    if not observations:
        raise ValueError("No CCI observations were supplied.")

    series = {
        "total-cci": "營造工程物價總指數",
        "cement-cci": "水泥指數",
        "rebar-cci": "鋼筋指數",
        "concrete-cci": "預拌混凝土指數",
    }
    settings["autocommit"] = False
    connection = pymysql.connect(**settings)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sync_runs (source, status) VALUES (%s, 'running')", (source,)
            )
            run_id = cursor.lastrowid
            series_ids = {}
            for display_order, (slug, name) in enumerate(series.items(), start=1):
                cursor.execute(
                    """
                    INSERT INTO index_series (slug, name, unit, base_year, display_order, is_active)
                    VALUES (%s, %s, 'index', 110, %s, TRUE)
                    ON DUPLICATE KEY UPDATE name = VALUES(name), base_year = VALUES(base_year),
                      display_order = VALUES(display_order), is_active = TRUE
                    """,
                    (slug, name, display_order * 10),
                )
                cursor.execute("SELECT id FROM index_series WHERE slug = %s", (slug,))
                series_ids[slug] = cursor.fetchone()["id"]

            count = 0
            for slug, points in observations.items():
                if slug not in series_ids:
                    continue
                for observed_on, value in points:
                    cursor.execute(
                        """
                        INSERT INTO index_observations (series_id, observed_on, value, source_url)
                        VALUES (%s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE value = VALUES(value), source_url = VALUES(source_url)
                        """,
                        (series_ids[slug], observed_on, value, source_url),
                    )
                    count += 1
            cursor.execute(
                "UPDATE sync_runs SET status = 'success', item_count = %s, finished_at = UTC_TIMESTAMP() "
                "WHERE id = %s",
                (count, run_id),
            )
        connection.commit()
        return count
    except Exception as error:
        connection.rollback()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sync_runs (source, status, error_message, finished_at) "
                    "VALUES (%s, 'failed', %s, UTC_TIMESTAMP())",
                    (source, str(error)[:65535]),
                )
            connection.commit()
        except Exception:
            connection.rollback()
        raise
    finally:
        connection.close()


def replace_synced_articles(category_slug, source, articles):
    """Atomically replace one feed's stored articles and record the sync result."""
    settings = _connection_settings()
    if not settings:
        raise RuntimeError("MySQL 尚未設定；無法將同步新聞寫入資料庫。")

    settings["autocommit"] = False
    news_categories = {
        "ai-real-estate": ("不動產新聞", 10),
        "urban-renewal-land-development": ("都市更新/土地開發新聞", 20),
        "building-regulations": ("建築法規新聞", 30),
        "material-prices": ("水泥／鋼筋價格", 40),
        "jensen-ai-trends": ("黃仁勳與 AI 趨勢", 50),
    }
    connection = pymysql.connect(**settings)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            # Keep category storage available for existing installations that
            # were initialized before a new RSS category was added.
            if category_slug in news_categories:
                category_name, display_order = news_categories[category_slug]
                cursor.execute(
                    """
                    INSERT INTO categories (slug, name, display_order, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    ON DUPLICATE KEY UPDATE name = VALUES(name),
                      display_order = VALUES(display_order), is_active = TRUE
                    """,
                    (category_slug, category_name, display_order),
                )
            cursor.execute(
                "INSERT INTO sync_runs (source, status) VALUES (%s, 'running')",
                (f"{source}:{category_slug}",),
            )
            run_id = cursor.lastrowid
            cursor.execute("SELECT id FROM categories WHERE slug = %s", (category_slug,))
            category = cursor.fetchone()
            if not category:
                raise RuntimeError(f"找不到新聞分類：{category_slug}。請先執行 database/seed.sql。")

            # Some pre-existing installations were created without the
            # article_tags foreign-key cascade.  Remove tag mappings
            # explicitly before replacing a feed, otherwise an orphaned old
            # mapping can be attached to a later article ID and show a wrong
            # publisher tag on every card.
            cursor.execute(
                """
                DELETE at
                FROM article_tags AS at
                INNER JOIN articles AS a ON a.id = at.article_id
                WHERE a.category_id = %s AND a.source_name LIKE %s
                """,
                (category["id"], f"RSS:{source}|%"),
            )
            cursor.execute(
                "DELETE FROM articles WHERE category_id = %s AND source_name LIKE %s",
                (category["id"], f"RSS:{source}|%"),
            )
            cursor.execute(
                """
                DELETE at
                FROM article_tags AS at
                LEFT JOIN articles AS a ON a.id = at.article_id
                WHERE a.id IS NULL
                """
            )
            for article in articles:
                cursor.execute(
                    """
                    INSERT INTO articles (
                      category_id, slug, article_type, title, published_on,
                      cover_image_url, summary, subtitle, original_title,
                      original_url, source_name, is_published
                    ) VALUES (%s, %s, 'news', %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    """,
                    (
                        category["id"], article["slug"], article["title"], article["date"],
                        article["cover_image"], article["summary"], article["subtitle"],
                        article["original_title"], article["original_url"],
                        f"RSS:{source}|{article['source_name']}",
                    ),
                )
                article_id = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO article_blocks (article_id, block_type, body, display_order) "
                    "VALUES (%s, 'paragraph', %s, 10)",
                    (article_id, article["body"]),
                )
                for tag_name in article["tags"]:
                    cursor.execute(
                        "INSERT INTO tags (name, slug) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE name = VALUES(name)",
                        (tag_name, article["tag_slugs"][tag_name]),
                    )
                    cursor.execute("SELECT id FROM tags WHERE name = %s", (tag_name,))
                    tag_id = cursor.fetchone()["id"]
                    cursor.execute(
                        "INSERT IGNORE INTO article_tags (article_id, tag_id) VALUES (%s, %s)",
                        (article_id, tag_id),
                    )
            cursor.execute(
                "UPDATE sync_runs SET status = 'success', item_count = %s, finished_at = UTC_TIMESTAMP() "
                "WHERE id = %s",
                (len(articles), run_id),
            )
        connection.commit()
        return len(articles)
    except Exception as error:
        connection.rollback()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sync_runs (source, status, error_message, finished_at) "
                    "VALUES (%s, 'failed', %s, UTC_TIMESTAMP())",
                    (f"{source}:{category_slug}", str(error)[:65535]),
                )
            connection.commit()
        except Exception:
            connection.rollback()
        raise
    finally:
        connection.close()


def _fetch_articles(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.id, a.slug, a.article_type, a.title, a.speaker, a.published_on,
                   a.duration_text, a.cover_image_url, a.embed_url, a.summary,
                   a.subtitle, a.original_title, a.original_url, a.source_name,
                   c.slug AS category_slug, c.name AS category_name
            FROM articles AS a
            LEFT JOIN categories AS c ON c.id = a.category_id
            WHERE a.is_published = TRUE
            ORDER BY a.published_on DESC, a.id DESC
            """
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        article_ids = [row["id"] for row in rows]
        placeholders = ", ".join(["%s"] * len(article_ids))
        cursor.execute(
            f"""
            SELECT at.article_id, t.name
            FROM article_tags AS at
            INNER JOIN tags AS t ON t.id = at.tag_id
            WHERE at.article_id IN ({placeholders})
            ORDER BY t.name
            """,
            article_ids,
        )
        tags_by_article = {}
        for tag in cursor.fetchall():
            tags_by_article.setdefault(tag["article_id"], []).append(f"#{tag['name']}")

        cursor.execute(
            f"""
            SELECT article_id, block_type, heading, body
            FROM article_blocks
            WHERE article_id IN ({placeholders})
            ORDER BY article_id, display_order, id
            """,
            article_ids,
        )
        content_by_article = {}
        for block in cursor.fetchall():
            content = content_by_article.setdefault(
                block["article_id"], {"paragraphs": [], "extended_analysis": []}
            )
            if block["block_type"] == "analysis":
                content["extended_analysis"].append(
                    {"title": block["heading"] or "重點分析", "desc": block["body"]}
                )
            else:
                content["paragraphs"].append(block["body"])

    rss_category_tags = {
        "ai-real-estate": ["不動產", "房市"],
        "urban-renewal-land-development": ["都市更新", "土地開發"],
        "building-regulations": ["建築法規", "建築管理"],
        "material-prices": ["水泥", "鋼筋", "建材行情"],
        "jensen-ai-trends": ["黃仁勳", "AI"],
    }
    articles = []
    for row in rows:
        source_name = row["source_name"] or ""
        is_rss = source_name.startswith("RSS:google-news-rss|")
        if is_rss:
            # Older databases can retain incorrect article_tags rows from a
            # previous installation.  RSS records already hold their real
            # publisher and category, so build these display tags from the
            # canonical article fields instead of trusting stale join rows.
            publisher = source_name.partition("|")[2]
            tags = [f"#{tag}" for tag in rss_category_tags.get(row["category_slug"], [])]
            if publisher:
                tags.append(f"#{publisher}")
        else:
            tags = tags_by_article.get(row["id"], [])
        content = content_by_article.get(row["id"], {"paragraphs": [], "extended_analysis": []})
        content.update(
            {
                # Keep previously synced Google News records consistent with the
                # current wording without requiring a full RSS re-sync.
                "subtitle": (
                    "此新聞由 Google News RSS 同步，內容請以原始媒體報導為準。"
                    if is_rss
                    else row["subtitle"] or row["summary"] or ""
                ),
                "original_title": row["original_title"] or row["title"],
                "original_url": row["original_url"] or "",
            }
        )
        articles.append(
            {
                "id": row["id"],
                "category": row["category_name"] or "未分類",
                "category_slug": row["category_slug"],
                "type": row["article_type"],
                "title": row["title"],
                "speaker": row["speaker"],
                "date": row["published_on"].isoformat(),
                "duration": row["duration_text"],
                "cover_image": row["cover_image_url"],
                "embed_url": row["embed_url"],
                "tags": tags,
                "summary": row["summary"] or "",
                "is_rss": is_rss,
                "full_content": content,
            }
        )
    return articles


def load_portal_overrides():
    """Load database-backed portal data. Return an empty dict if DB is unavailable."""
    if not HAS_PYMYSQL:
        return {}
    try:
        settings = _connection_settings()
        if not settings:
            return {}

        connection = pymysql.connect(**settings)
        try:
            overrides = {}
            with connection.cursor() as cursor:
                cursor.execute("SELECT setting_value FROM site_settings WHERE setting_key = 'hero'")
                hero = cursor.fetchone()
                if hero:
                    value = hero["setting_value"]
                    overrides["hero"] = json.loads(value) if isinstance(value, str) else value

                cursor.execute(
                    """
                    SELECT source, finished_at
                    FROM sync_runs
                    WHERE status = 'success' AND source = 'google-news-rss:jensen-ai-trends'
                    ORDER BY finished_at DESC, id DESC LIMIT 1
                    """
                )
                latest_jensen_sync = cursor.fetchone()
                if latest_jensen_sync and latest_jensen_sync["finished_at"]:
                    # MySQL TIMESTAMP is returned by PyMySQL without tzinfo;
                    # sync runs are explicitly written with UTC_TIMESTAMP().
                    overrides["jensen_news_updated_at"] = latest_jensen_sync["finished_at"].replace(
                        tzinfo=timezone.utc
                    )

                cursor.execute(
                    """
                    SELECT series.slug, observation.observed_on, observation.value
                    FROM index_series AS series
                    INNER JOIN index_observations AS observation ON observation.series_id = series.id
                    WHERE series.is_active = TRUE
                    ORDER BY observation.observed_on, series.display_order
                    """
                )
                observations = cursor.fetchall()

            if observations:
                # The crawler retains history, while the public chart deliberately
                # displays only the newest twelve official monthly observations.
                dates = sorted({row["observed_on"] for row in observations})[-12:]
                by_series = {}
                for row in observations:
                    by_series.setdefault(row["slug"], {})[row["observed_on"]] = float(row["value"])
                overrides["cci_chart_data"] = {
                    "months": [date.strftime("%Y/%m") for date in dates],
                    "total_index": [by_series.get("total-cci", {}).get(date) for date in dates],
                    "cement_index": [by_series.get("cement-cci", {}).get(date) for date in dates],
                    "rebar_index": [by_series.get("rebar-cci", {}).get(date) for date in dates],
                    "concrete_index": [by_series.get("concrete-cci", {}).get(date) for date in dates],
                }

            articles = _fetch_articles(connection)
            if articles:
                overrides["articles"] = articles
            return overrides
        finally:
            connection.close()
    except Exception:
        return {}
