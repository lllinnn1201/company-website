-- Company Portal MySQL schema
-- Run this file in Navicat: New Query -> Open SQL File -> Run.

CREATE DATABASE IF NOT EXISTS ai_news
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE ai_news;

CREATE TABLE IF NOT EXISTS site_settings (
  setting_key VARCHAR(100) NOT NULL,
  setting_value JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS categories (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  slug VARCHAR(100) NOT NULL,
  name VARCHAR(100) NOT NULL,
  display_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_categories_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS articles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  category_id BIGINT UNSIGNED NULL,
  slug VARCHAR(180) NOT NULL,
  article_type ENUM('news', 'video') NOT NULL DEFAULT 'news',
  title VARCHAR(500) NOT NULL,
  speaker VARCHAR(200) NULL,
  published_on DATE NOT NULL,
  duration_text VARCHAR(50) NULL,
  cover_image_url VARCHAR(2048) NULL,
  embed_url VARCHAR(2048) NULL,
  summary TEXT NULL,
  subtitle TEXT NULL,
  original_title VARCHAR(500) NULL,
  original_url VARCHAR(2048) NULL,
  source_name VARCHAR(200) NULL,
  is_published BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_articles_slug (slug),
  KEY idx_articles_listing (category_id, is_published, published_on),
  CONSTRAINT fk_articles_category FOREIGN KEY (category_id)
    REFERENCES categories (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_blocks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL,
  block_type ENUM('paragraph', 'analysis') NOT NULL,
  heading VARCHAR(300) NULL,
  body TEXT NOT NULL,
  display_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_article_blocks (article_id, display_order),
  CONSTRAINT fk_article_blocks_article FOREIGN KEY (article_id)
    REFERENCES articles (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tags (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  slug VARCHAR(100) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tags_name (name),
  UNIQUE KEY uq_tags_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_tags (
  article_id BIGINT UNSIGNED NOT NULL,
  tag_id BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (article_id, tag_id),
  CONSTRAINT fk_article_tags_article FOREIGN KEY (article_id)
    REFERENCES articles (id) ON DELETE CASCADE,
  CONSTRAINT fk_article_tags_tag FOREIGN KEY (tag_id)
    REFERENCES tags (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_series (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  slug VARCHAR(100) NOT NULL,
  name VARCHAR(100) NOT NULL,
  unit VARCHAR(30) NOT NULL DEFAULT 'index',
  base_year SMALLINT UNSIGNED NULL,
  display_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (id),
  UNIQUE KEY uq_index_series_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_observations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  series_id BIGINT UNSIGNED NOT NULL,
  observed_on DATE NOT NULL,
  value DECIMAL(10,2) NOT NULL,
  source_url VARCHAR(2048) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_index_observations (series_id, observed_on),
  KEY idx_index_observations_date (observed_on),
  CONSTRAINT fk_index_observations_series FOREIGN KEY (series_id)
    REFERENCES index_series (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sync_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source VARCHAR(100) NOT NULL,
  status ENUM('running', 'success', 'failed') NOT NULL,
  item_count INT UNSIGNED NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP NULL,
  PRIMARY KEY (id),
  KEY idx_sync_runs_source_started (source, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
