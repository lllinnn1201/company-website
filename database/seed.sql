-- Optional initial data. Run after schema.sql.
USE ai_news;

INSERT INTO categories (slug, name, display_order) VALUES
  ('ai-real-estate', 'AI 房地產', 10),
  ('urban-renewal-land-development', '都市更新/土地開發新聞', 20),
  ('building-regulations', '建築法規新聞', 30),
  ('material-prices', '建材／原物料', 40),
  ('jensen-ai-trends', '黃仁勳 AI 趨勢', 50)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  display_order = VALUES(display_order);

INSERT INTO index_series (slug, name, unit, display_order) VALUES
  ('total-cci', '營造工程物價總指數', 'index', 10),
  ('cement-cci', '水泥指數', 'index', 20),
  ('rebar-cci', '鋼筋指數', 'index', 30),
  ('concrete-cci', '預拌混凝土指數', 'index', 40)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  display_order = VALUES(display_order);

-- CCI observations are downloaded from the official DGBAS statistics database
-- by POST /api/sync/cci; no placeholder values are seeded.

INSERT INTO site_settings (setting_key, setting_value) VALUES
  ('hero', JSON_OBJECT(
    'badge', '產業情報',
    'title', '產業動態與營造材料資訊網',
    'subtitle', '最新產業動態、不動產、都市更新、建築法規與營建資訊'
  ))
ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value);
