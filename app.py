# -*- coding: utf-8 -*-
"""
=============================================================================
專案名稱：丞石建築開發股份有限公司 不動產資訊 Portal 網站
後端主程式：app.py
技術 stack：Python 3.x, Flask, Jinja2
說明：此程式為 Flask 後端 Controller 服務，負責初始化 Web 伺服器，
      管理資訊資料 (YouTube 趨勢影音、不動產新聞、水泥與鋼筋價格行情新聞)，並渲染前端 Index 頁面。
=============================================================================
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import hashlib
import hmac
import os
import re
import ssl
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from flask import Flask, jsonify, render_template, request
from database import (
    database_is_configured,
    load_portal_overrides,
    replace_cci_observations,
    replace_synced_articles,
)

# 初始化 Flask 應用程式
app = Flask(__name__)
# 開發期間修改 templates 目錄下的 HTML/CSS 後，下一個請求即重新載入模板。
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


# Google News 是搜尋結果的公開 RSS 介面；只讀取其提供的標題、摘要、日期和原文連結，
# 不擷取或重製新聞內文。RSS 僅由每日排程讀取，首頁一律讀取 MySQL。
NEWS_FEEDS = {
    "ai-real-estate": {"query": "台灣 不動產", "tags": ["不動產", "房市"]},
    "urban-renewal-land-development": {
        "query": "台灣 都市更新 土地開發",
        "tags": ["都市更新", "土地開發"],
    },
    "building-regulations": {
        "query": "台灣 建築法規 建築管理",
        "tags": ["建築法規", "建築管理"],
    },
    "material-prices": {"query": "台灣 水泥 鋼筋 價格", "tags": ["水泥", "鋼筋", "建材行情"]},
    "jensen-ai-trends": {"query": "黃仁勳 AI", "tags": ["黃仁勳", "AI"]},
}
RSS_SOURCE = "google-news-rss"
CCI_SOURCE = "dgbas-cci"
CCI_QUERY_ENDPOINT = "https://nstatdb.dgbas.gov.tw/dgbasAll/webQuery.aspx"
CCI_SOURCE_URL = "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?funid=queryXls&sys=210"
CCI_SERIES = (
    ("total-cci", "221141010:0/0/0/1000/0/"),
    ("cement-cci", "221141010:0/0/0/1101001/0/"),
    ("rebar-cci", "221141010:0/0/0/1104016/0/"),
    ("concrete-cci", "221141010:0/0/0/1101002/0/"),
)
ARTICLE_PLACEHOLDER = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1200' height='675'"
    "%3E%3Crect width='100%25' height='100%25' fill='%230f172a'/%3E"
    "%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle'"
    "fill='%2367e8f9' font-family='Arial,sans-serif' font-size='48'%3EAI NEWS%3C/text%3E%3C/svg%3E"
)


def _plain_text(value):
    """將 RSS 摘要中的 HTML 轉成可安全顯示的純文字。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value or ""))).strip()


def _slugify(value):
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:70] or "news"


def fetch_google_news(category_slug):
    """Read one configured RSS feed and normalize it for MySQL storage."""
    feed = NEWS_FEEDS[category_slug]
    now = datetime.now(timezone.utc)
    query = urlencode({"q": feed["query"], "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})
    feed_url = f"https://news.google.com/rss/search?{query}"
    request = Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (Company News Portal RSS Reader)"})
    with urlopen(request, timeout=15) as response:
        root = ElementTree.fromstring(response.read())

    items = []
    for node in root.findall("./channel/item")[:8]:
        title = _plain_text(node.findtext("title"))
        link = (node.findtext("link") or "").strip()
        source_node = node.find("source")
        source = _plain_text(source_node.text if source_node is not None else "Google News")
        summary = _plain_text(node.findtext("description"))
        try:
            date = parsedate_to_datetime(node.findtext("pubDate")).astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            date = now.date().isoformat()
        if not title or not link:
            continue
        digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]
        body = summary or "此新聞未提供摘要，請開啟原始報導查看完整內容。"
        tags = [*feed["tags"], source]
        items.append({
            "slug": f"rss-{category_slug}-{_slugify(title)}-{digest}",
            "title": title,
            "date": date,
            "cover_image": ARTICLE_PLACEHOLDER,
            "tags": tags,
            "tag_slugs": {tag: _slugify(tag) for tag in tags},
            "summary": summary or f"來源：{source}。請開啟原始報導閱讀完整內容。",
            "subtitle": "此新聞由 Google News RSS 同步，內容請以原始媒體報導為準。",
            "body": body,
            "original_title": f"{source}：{title}",
            "original_url": link,
            "source_name": source,
        })
    return items


def _month_start(value):
    return value.replace(day=1)


def _shift_month(value, offset):
    """Return the first day of the month ``offset`` months from ``value``."""
    month_index = value.year * 12 + value.month - 1 + offset
    return value.replace(year=month_index // 12, month=month_index % 12 + 1, day=1)


def _roc_year_month(value):
    return f"{value.year - 1911:03d}{value.month:02d}"


def fetch_dgbas_cci():
    """Download the official CCI CSV for the chart's three monthly series.

    The DGBAS query service returns values only (one row per available month),
    so the returned rows are aligned to the newest available requested months.
    Requesting a little more history makes the job resilient when the new
    monthly release has not been published yet.
    """
    # CCI is a monthly release.  Query through the preceding calendar month so
    # the result has no trailing unpublished row (the service does not include
    # dates in its CSV output).
    latest_complete_month = _shift_month(
        _month_start(datetime.now(timezone(timedelta(hours=8))).date()), -1
    )
    start_month = _shift_month(latest_complete_month, -17)
    fields = "[" + "|".join(f"{field} $$" for _, field in CCI_SERIES) + "|]"
    query = urlencode({
        "sys": "250",
        "funid": "queryXls",
        "ymf": _roc_year_month(start_month),
        "ymt": _roc_year_month(latest_complete_month),
        "cyc": "1",
        "fldlist": fields,
        "codelist": "[]",
        "vba": "1",
        "outmode": "20",
    })
    source_url = f"{CCI_QUERY_ENDPOINT}?{query}"
    request = Request(source_url, headers={"User-Agent": "Mozilla/5.0 (Company Portal CCI Sync)"})
    ssl_context = ssl.create_default_context()
    # DGBAS currently presents a certificate chain that lacks a Subject Key
    # Identifier. Python 3.13+ rejects it in X509 strict mode; keep normal
    # CA and hostname validation while relaxing only that compatibility check.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    with urlopen(request, timeout=30, context=ssl_context) as response:
        payload = response.read().decode("utf-8-sig")

    rows = []
    for raw_line in payload.splitlines():
        values = [value.strip() for value in raw_line.split("|") if value.strip()]
        if len(values) != len(CCI_SERIES):
            continue
        try:
            rows.append([float(value.replace(",", "")) for value in values])
        except ValueError:
            continue
    if not rows:
        raise RuntimeError("DGBAS returned no usable CCI data.")

    requested_months = [_shift_month(start_month, offset) for offset in range(18)]
    months = requested_months[-len(rows):]
    observations = {slug: [] for slug, _ in CCI_SERIES}
    for observed_month, values in zip(months, rows):
        for (slug, _), value in zip(CCI_SERIES, values):
            observations[slug].append((observed_month, value))
    return source_url, observations

# -----------------------------------------------------------------------------
# 模擬資料庫 / 資料提供層 (Mock Data)
# -----------------------------------------------------------------------------

# 功能：獲取 Portal 網站所需的各類別資訊與圖表資料
# 流程：定義文章與影音資料 > 彙整英雄區公告 > 依類別順序合併文章列表 > 回傳Portal資料字典
def get_portal_data():
    """
    獲取 Portal 網站所需的資料。
    包含主要類別：
    1. 不動產新聞 (新聞與專題分析)
    2. 都市更新／土地開發新聞
    3. 建築法規新聞
    4. 水泥/鋼筋價格 新聞 (水泥價格新聞與鋼筋價格新聞)
    5. 黃仁勳與 AI 趨勢 (YouTube 影音與新聞報導)
    """
    
# 1. 不動產新聞
    ai_real_estate_news = [
        {
            "id": 4,
            "category": "不動產新聞",
            "type": "news",
            "title": "【智慧營造】BIM 系統整合應用：工期縮短 25% 且施工安全提升雙倍",
            "date": "2026-07-28",
            "cover_image": "https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#智慧建築", "#BIM整合", "#工地安全", "#營造科技"],
            "summary": "整合 BIM 模型自動偵測施工圖面衝突並建議最佳工程排程。導入電腦視覺監視器，即時預警工地安全隱患，全面優化資材採購預測，有效降低營建廢棄物與材料成本逾 15%。",
            "full_content": {
                "subtitle": "國內各大建設公司積極引進 5D BIM 技術與智慧工地監控，邁向高效率與零災害施工新紀元。",
                "paragraphs": [
                    "隨著國內營造業勞工短缺與資材價格波動，各大建設公司紛紛加速推動智慧工地轉型。最新調查顯示，導入 5D BIM（建築資訊模型）系統的工程專案，平均能將建案工期縮短 25%，材料損耗降低 15% 以上。",
                    "5D BIM 系統結合了時間（4D）與成本（5D）維度，施工團隊能在開工前於電腦中模擬每一階段的施工順序，及早發現結構鋼筋、水電管線與消防設備的碰闖衝突，避免過去開工後因圖面不合導致的拆除重做成本。",
                    "此外，工地現場同步部署了電腦視覺監控設備，能自動辨識工人在危險區域是否正確配戴安全帽與防墜繩，若發現異常將立即發出語音警示與通報現場指揮官，大幅降低工地職災發生機率。"
                ],
                "extended_analysis": [
                    {
                        "title": "碰撞檢查零施工衝突",
                        "desc": "在電腦中預先解決鋼筋與管線衝突，避免開工後因現場拆改造成的重大成本浪費。"
                    },
                    {
                        "title": "動態工期與資金掌控",
                        "desc": "結合 5D 時間成本模型，精準掌握各期估驗款發放與材料進場時間。"
                    },
                    {
                        "title": "智慧工安零災害目標",
                        "desc": "透過全天候監控警示，主動預防高空墜落與違規作業風險。"
                    }
                ],
                "original_title": "臺灣各大建設引進 BIM 技術，推進綠建築與智慧工地新標準",
                "original_url": "https://example.com/news/bim-construction"
            }
        },
        {
            "id": 5,
            "category": "不動產新聞",
            "type": "news",
            "title": "【不動產估價】動態估價模型問世：精準預測捷運沿線房價與土地價值",
            "date": "2026-07-30",
            "cover_image": "https://images.unsplash.com/photo-1560518883-ce09059eeffa?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#不動產估價", "#大數據分析", "#建案開發", "#PropTech"],
            "summary": "結合歷年實價登錄、人口移動與商圈發展大數據，建立動態估價模型。協助開發部門在 5 分鐘內完成土地開發可行性評估與投報率分析，預測準確度高達 94%。",
            "full_content": {
                "subtitle": "PropTech 不動產科技重大突破，深度學習大數據模型協助開發團隊精準評估購地成本與建案未來利潤。",
                "paragraphs": [
                    "在不動產開發決策中，土地評估與房價預測是最核心也最耗時的環節。最新問世的動態不動產估價模型，成功整合了過去十年超過百萬筆的實價登錄資料、周邊捷運站流、商圈發展指數與人口遷徙趨勢。",
                    "開發部門過去需要花費數天進行市場調查與週邊競品比價，現在只需輸入目標土地的地號或座標，估價模型即可在 5 分鐘內產出包含「未來 1~3 年預估售價」、「周邊同類型建案去化速度」與「最佳產品坪數規劃建議」的完整可行性報告。",
                    "實際驗證顯示，該模型對捷運沿線與新開發重劃區的房價預測精準度達到 94%，能有效降低建設公司在購地競標時的溢價風險，並協助規劃最符合市場剛性需求的建案格局。"
                ],
                "extended_analysis": [
                    {
                        "title": "5分鐘土地評估報告",
                        "desc": "極速產出購地可行性試算，掌握市場去化速度與最佳開價區間。"
                    },
                    {
                        "title": "大數據精準產品定位",
                        "desc": "分析周邊人口結構與購屋偏好，建議最佳的二房/三房坪數配置比例。"
                    },
                    {
                        "title": "避開購地溢價風險",
                        "desc": "透過多維度動態比價演算法，降低土地開發階段人為估值誤差。"
                    }
                ],
                "original_title": "不動產科技（PropTech）新突破：深度學習引領土地開發決策",
                "original_url": "https://example.com/news/proptech-valuation"
            }
        }
    ]

    # 2. 水泥/鋼筋價格 新聞
    material_price_news = [
        {
            "id": 6,
            "category": "水泥/鋼筋價格 新聞",
            "type": "news",
            "title": "【水泥行情】碳費開徵與綠色建材需求升溫：國內預拌混凝土價格趨勢分析",
            "date": "2026-07-29",
            "cover_image": "https://images.unsplash.com/photo-1589939705384-5185137a7f0f?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#水泥價格", "#預拌混凝土", "#低碳水泥", "#建材成本"],
            "summary": "受環境部碳費政策與綠建築標章推動，國內低碳水與預拌混凝土價格呈現穩健趨勢。各大建廠積極轉型減碳技術，影響營造工程發包資材預算與採購策略。",
            "full_content": {
                "subtitle": "深度剖析水泥大廠產能調定、進口水泥課稅政策與營造廠發包成本影響。",
                "paragraphs": [
                    "隨著綠建築與低碳營造趨勢明確，國內水泥與混凝土產業正迎來近五年最大規模的產品轉型。市場最新統計顯示，符合低碳認證的波特蘭石灰石水泥與墁砌水泥需求年增超過 30%。",
                    "各大水泥大廠表示，因應碳費基期設定與綠色金融授信標準，未來低碳混凝土將逐漸成為大型建案與公共工程的標準配備。雖然初發包單價略微上揚，但能顯著提升建案價值與綠建築等級。",
                    "專家建議，建設公司與發包部門應提前與混凝土供應商簽訂長期框架協議，並透過 5D BIM 材料預測系統控制庫存，確保建案工期不受原物料價格波動影響。"
                ],
                "extended_analysis": [
                    {
                        "title": "低碳綠色水泥浪潮",
                        "desc": "低碳混凝土認證成為建案行銷與ESG永續報告的核心亮點。"
                    },
                    {
                        "title": "採購合約框架優化",
                        "desc": "與主要供應商簽定長期量購鎖價合約，降低通膨波動衝擊。"
                    },
                    {
                        "title": "工地現場算量精算",
                        "desc": "嚴格控制混凝土灌漿現場損耗率，精算開工估驗進度與資材調度。"
                    }
                ],
                "original_title": "營造資材行情報導：國內水泥價格與碳費政策影響評估",
                "original_url": "https://example.com/news/cement-price-trends"
            }
        },
        {
            "id": 7,
            "category": "水泥/鋼筋價格 新聞",
            "type": "news",
            "title": "【鋼筋盤價】鐵礦砂與廢鋼走勢震盪：豐興鋼筋最新開盤價與發包策略建議",
            "date": "2026-07-31",
            "cover_image": "https://images.unsplash.com/photo-1535813547-99c456a41d4a?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#鋼筋價格", "#廢鋼行情", "#豐興盤價", "#資材採購"],
            "summary": "受鐵礦砂價格走勢與廢鋼原料成本影響，國內大廠豐興公佈最新鋼筋盤價趨勢。採購部門應精準掌抓交貨期程與採購點，彈性調整結構發包步調。",
            "full_content": {
                "subtitle": "分析最新一期鋼筋廠開盤價、廢鋼收購價行情，協助營造廠管理鋼筋發包與備料週期。",
                "paragraphs": [
                    "國內鋼筋指標大廠豐興公佈最新一週盤價，主要受到廢鋼原料價格小幅拉回與鐵礦砂走勢震盪影響，鋼筋買氣呈現觀望與穩健拿貨並存態勢。",
                    "分析師指出，由於科技廠房新建與公共工程發包需求強勁，國內鋼筋基礎需求依然穩固。目前鋼筋盤價處於合理區間，營造廠與建設公司若有下半年即將開工之結構專案，可逢低進行部分分批備料。",
                    "此外，高強度鋼筋 (如 SD420) 在高層耐震建築的應用比率提升，能有效減少鋼筋用量與綁紮人工，建議設計與結構發包階段多加考量。"
                ],
                "extended_analysis": [
                    {
                        "title": "分批鎖價降低風險",
                        "desc": "採取分批下單與分期交付策略，分散金屬原料波動風險。"
                    },
                    {
                        "title": "高強度鋼筋替代效益",
                        "desc": "採用高強度耐震鋼筋降低總用鋼噸數，節省綁紮工時與運費成本。"
                    },
                    {
                        "title": "廢鋼與盤價即時連動",
                        "desc": "建立採購即時預警機制，掌握每週鋼筋盤價開盤最佳採購時機。"
                    }
                ],
                "original_title": "鋼鐵產業新聞：豐興最新鋼筋盤價與廢鋼行情分析",
                "original_url": "https://example.com/news/rebar-price-analysis"
            }
        }
    ]

    # 3.1 黃仁勳與 AI 趨勢 - YouTube 影音
    ai_trends_videos = [
        {
            "id": 1,
            "category": "黃仁勳與 AI 趨勢",
            "type": "video",
            "title": "黃仁勳 Computex 主題演講：AI 物理學與 Omniverse 數位雙生技術",
            "speaker": "黃仁勳 (Jensen Huang)",
            "date": "2024-06-02",
            "duration": "1 小時 45 分鐘",
            "embed_url": "https://www.youtube.com/embed/K84ly8_m7sA",
            "cover_image": "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#PhysicalAI", "#Omniverse", "#數位雙生", "#建築科技"],
            "summary": "NVIDIA 執行長黃仁勳展示 Physical AI 與 Omniverse 數位雙生技術。預告生成式 AI 將重塑營造、建築設計與實體產業的運作流程，透過數位雙生實現建築前期的精準模擬與風險預測。",
            "full_content": {
                "subtitle": "探索工業數位雙生（Digital Twins）與實體 AI 如何顛覆建築設計與大型營造工程專案。",
                "paragraphs": [
                    "在 Computex 主題演講中，NVIDIA 執行長黃仁勳發表了針對實體產業的重磅技術方案——Physical AI 與新一代 Omniverse 模擬平台。他指出，未來的營造與建築產業不再僅止於傳統圖紙或 3D 模型，而是能於虛擬空間中 1:1 建立高精度的物理數位雙生模型。",
                    "透過將 BIM (建築資訊模型) 數據匯入 Omniverse 系統，工程團隊可以在開工前模擬極端氣候、地震應力、採光與風場效益，甚至即時模擬重型機械於工地現場的作業軌跡。這項創新大幅降減了施工圖面衝突與重複派工成本。",
                    "黃仁勳強調：「實體產業是整體經濟的骨幹。透過將 AI 注入物理學與空間模擬，營造業將能以更高的安全性、更短的工期，實現前所未有的綠色智慧建築計畫。」"
                ],
                "extended_analysis": [
                    {
                        "title": "工程風險前置預測",
                        "desc": "在開工前透過數位雙生進行全場景模擬，提前發現結構設計缺陷與施工死角。"
                    },
                    {
                        "title": "自動化排程優化",
                        "desc": "結合 AI 空間演算法，精準排定吊車與混凝土車搬運時程，避免工地塞車與工期延誤。"
                    },
                    {
                        "title": "綠建築能耗精算",
                        "desc": "即時模擬建築物未來 50 年的日光照射與熱能傳導，最佳化外牆材料與空調設計。"
                    }
                ],
                "original_title": "YouTube 原創影片：NVIDIA Computex Keynote",
                "original_url": "https://www.youtube.com/watch?v=K84ly8_m7sA"
            }
        },
        {
            "id": 2,
            "category": "黃仁勳與 AI 趨勢",
            "type": "video",
            "title": "黃仁勳專訪：AI 革命下的實體產業與營造建築數位轉型",
            "speaker": "黃仁勳 (Jensen Huang)",
            "date": "2024-03-18",
            "duration": "45 分鐘",
            "embed_url": "https://www.youtube.com/embed/Y2F8yisiS6E",
            "cover_image": "https://images.unsplash.com/photo-1541888946425-d0fbb186a5b7?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#實體產業轉型", "#空間運算", "#智慧營造", "#生成式AI"],
            "summary": "黃仁勳深入解析 AI 如何走入重工業與建築工程。透過加速運算與空間 AI 模組，建設公司能將工程建案結構設計優化時間從數週縮短至數小時，達成綠建築與智慧營造的極致效益。",
            "full_content": {
                "subtitle": "專訪談營造業與重工業如何迎頭趕上科技浪潮，利用空間演算法實現建案自動優化。",
                "paragraphs": [
                    "黃仁勳在專訪中深入探討了 AI 在傳統重工業與建築開發領域的實務落地。他提及，傳統建築工程往往耗費大量人工時間進行結構計算、水電管線避讓與建材採購調度，而加速運算與深度學習技術正在徹底翻轉這個現狀。",
                    "透過生成式 AI 演算法，建築師只需輸入基地幾何條件、容積率限制與採光需求，系統便能在幾秒鐘內產生數百組結構最穩固、採光最佳的設計方案，並直接產出精準的材料用量預算估算表。",
                    "「科技不是要取代營造專業，而是要成為建設公司最強大的工具。」黃仁勳表示，優先將先進運算工具導入決策流程的建設企業，將能在品質、效率與可持續性上大幅超越同業。"
                ],
                "extended_analysis": [
                    {
                        "title": "設計方案極速生成",
                        "desc": "演算法自動產出數百組優化配置，大幅縮短開發前的建築規劃與可行性試算時間。"
                    },
                    {
                        "title": "精準算量降低浪費",
                        "desc": "自動比對圖面與材料規格，有效控制營建廢棄物與資材囤積成本。"
                    },
                    {
                        "title": "品質與安全雙重提升",
                        "desc": "自動化演算法比對各類法規與防震標準，降低人工圖面審查疏漏風險。"
                    }
                ],
                "original_title": "YouTube 原創影片：Jensen Huang Interview",
                "original_url": "https://www.youtube.com/watch?v=Y2F8yisiS6E"
            }
        }
    ]

    # 3.2 黃仁勳與 AI 趨勢 - 新聞報導
    ai_trends_news = [
        {
            "id": 3,
            "category": "黃仁勳與 AI 趨勢",
            "type": "news",
            "title": "黃仁勳揭示 AI 工廠與數位雙生浪潮：重塑國內實體供應鏈與營造工程",
            "date": "2026-07-25",
            "cover_image": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80",
            "tags": ["#AI工廠", "#Omniverse", "#供應鏈", "#營造革新"],
            "summary": "NVIDIA 發表最新產業白皮書，黃仁勳強調實體世界與數位空間的結合將使製造與建築營造邁入『AI 工廠』時代，大幅提升國內基礎建設的開發效率與安全性。",
            "full_content": {
                "subtitle": "數位雙生技術走出實驗室，國內各大建築與重工業巨頭紛紛建置 AI 工廠模擬系統。",
                "paragraphs": [
                    "NVIDIA 最新發布的產業分析報導中，黃仁勳明確指出：「AI 的下一個巨大浪潮將是實體 AI（Physical AI）。」這意味著人工智慧將直接理解物理定律、幾何空間與重量結構，徹底變革大規模建築營造與重工業製造。",
                    "目前國內已有多家頂尖建築設計事務所與營造集團採用 Omniverse 系統，將 BIM 模型結合即時物理引擎，在開工前預先演算施工過程中的結構承載力與天候防護能力。",
                    "專家預測，這波實體 AI 與數位雙生浪潮將在未來五年內為營造產業帶來超過 3,000 億美元的效率提升，實現更高品質、更低能耗的世代建築工程。"
                ],
                "extended_analysis": [
                    {
                        "title": "實體世界物理演算",
                        "desc": "AI 模型深入理解重力、材料強度與熱傳導，提供前所未有的工程預測精度。"
                    },
                    {
                        "title": "國內團隊雲端協作",
                        "desc": "設計師與施工團隊於同一數位雙生空間中即時修改圖面，溝通零時差。"
                    },
                    {
                        "title": "減碳與永續經營",
                        "desc": "精確模擬建材生命週期碳足跡，協助開發商輕鬆達成 ESG 低碳淨零目標。"
                    }
                ],
                "original_title": "NVIDIA Newsroom: Physical AI Reshaping Heavy Industries",
                "original_url": "https://nvidianews.nvidia.com"
            }
        }
    ]

    # 4. 行政院主計總處 — 營造工程物價指數 (CCI) 近 12 個月核心建材數據 (基期 100)
    # CCI is intentionally database-only.  Never fall back to representative
    # numbers: an empty chart is preferable to presenting synthetic statistics.
    cci_chart_data = {
        "months": [],
        "total_index": [],
        "cement_index": [],
        "rebar_index": [],
        "concrete_index": [],
    }

    # 設定英雄區公告內容字典
    hero_info = {
        # 設定標籤
        "badge": "資訊專區",
        # 設定標題
        "title": "產業動態與營造材料資訊網",
        # 設定副標題文字（調整黃仁勳與 AI 趨勢至最後）
        "subtitle": "最新產業動態、不動產、都市更新、建築法規與營建資訊"
    }

    # MySQL 設定完成後，優先使用資料庫內容；尚未匯入文章時保留既有展示資料。
    database_data = load_portal_overrides()
    hero_info = database_data.get("hero", hero_info)
    cci_chart_data = database_data.get("cci_chart_data", cci_chart_data)
    database_articles = database_data.get("articles", [])
    # 這兩個分類由 RSS／MySQL 提供；尚未同步時保留空列表而非顯示示範新聞。
    urban_renewal_land_development_news = []
    building_regulations_news = []
    if database_articles:
        ai_real_estate_news = [
            article for article in database_articles if article["category_slug"] == "ai-real-estate"
        ]
        material_price_news = [
            article for article in database_articles if article["category_slug"] == "material-prices"
        ]
        urban_renewal_land_development_news = [
            article
            for article in database_articles
            if article["category_slug"] == "urban-renewal-land-development"
        ]
        building_regulations_news = [
            article for article in database_articles if article["category_slug"] == "building-regulations"
        ]
        ai_trends_videos = [
            article
            for article in database_articles
            if article["category_slug"] == "jensen-ai-trends" and article["type"] == "video"
        ]
        ai_trends_news = [
            article
            for article in database_articles
            if article["category_slug"] == "jensen-ai-trends" and article["type"] == "news"
        ]

    # 合併所有文章與影音資料，將黃仁勳與 AI 趨勢移至最後面
    all_articles = (
        ai_real_estate_news
        + urban_renewal_land_development_news
        + building_regulations_news
        + material_price_news
        + ai_trends_videos
        + ai_trends_news
    )

    # 回傳資料字典
    return {
        # 回傳英雄區資訊
        "hero": hero_info,
        # 回傳 AI 趨勢影音
        "ai_trends_videos": ai_trends_videos,
        # 回傳 AI 趨勢新聞
        "ai_trends_news": ai_trends_news,
        # 供前端標註新聞的最後成功同步時間
        "jensen_news_updated_at": (
            database_data["jensen_news_updated_at"].astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
            if database_data.get("jensen_news_updated_at") else None
        ),
        # 回傳不動產新聞
        "ai_real_estate_news": ai_real_estate_news,
        # 回傳都市更新／土地開發新聞
        "urban_renewal_land_development_news": urban_renewal_land_development_news,
        # 回傳建築法規新聞
        "building_regulations_news": building_regulations_news,
        # 回傳建材價格新聞
        "material_price_news": material_price_news,
        # 回傳 CCI 圖表資料
        "cci_chart_data": cci_chart_data,
        # 回傳全部文章列表
        "all_articles": all_articles
    }


# -----------------------------------------------------------------------------
# 路由 (Routes)
# -----------------------------------------------------------------------------

@app.route("/")
def index():
    """
    首頁路由 Controller
    """
    portal_data = get_portal_data()
    return render_template("index.html", data=portal_data)


def _sync_request_is_authorized():
    """Accept Vercel Cron's bearer token (and the same token for manual syncs)."""
    secret = os.getenv("CRON_SECRET")
    if not secret:
        return False
    authorization = request.headers.get("Authorization", "")
    return hmac.compare_digest(authorization, f"Bearer {secret}")


@app.route("/api/sync/news", methods=["GET", "POST"])
def sync_all_news():
    """Daily job: fetch all configured feeds once, then persist them in MySQL."""
    if not _sync_request_is_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if not database_is_configured():
        return jsonify({"ok": False, "error": "MySQL 尚未設定"}), 503

    results = {}
    errors = {}
    for category_slug in NEWS_FEEDS:
        try:
            items = fetch_google_news(category_slug)
            if not items:
                raise RuntimeError("RSS 沒有可儲存的新聞項目")
            results[category_slug] = replace_synced_articles(category_slug, RSS_SOURCE, items)
        except Exception as error:
            app.logger.exception("新聞同步失敗（%s）", category_slug)
            errors[category_slug] = str(error)

    status_code = 200 if not errors else 502
    return jsonify({"ok": not errors, "synced": results, "errors": errors}), status_code


@app.route("/api/sync/cci", methods=["GET", "POST"])
def sync_cci():
    """Fetch official DGBAS CCI observations and upsert them into MySQL."""
    if not _sync_request_is_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if not database_is_configured():
        return jsonify({"ok": False, "error": "MySQL is not configured."}), 503
    try:
        source_url, observations = fetch_dgbas_cci()
        count = replace_cci_observations(CCI_SOURCE, source_url, observations)
        months = max(len(points) for points in observations.values())
        return jsonify({
            "ok": True,
            "source": CCI_SOURCE_URL,
            "months": months,
            "observations": count,
        })
    except Exception as error:
        app.logger.exception("DGBAS CCI sync failed")
        return jsonify({"ok": False, "error": str(error)}), 502


@app.post("/api/sync/jensen-huang")
def sync_jensen_huang_news():
    """Backward-compatible endpoint; use /api/sync/news for the daily job."""
    if not _sync_request_is_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if not database_is_configured():
        return jsonify({"ok": False, "error": "MySQL 尚未設定"}), 503
    try:
        items = fetch_google_news("jensen-ai-trends")
        count = replace_synced_articles("jensen-ai-trends", RSS_SOURCE, items)
        return jsonify({"ok": True, "count": count})
    except Exception as error:
        app.logger.exception("黃仁勳新聞同步失敗")
        return jsonify({"ok": False, "error": str(error)}), 502


# -----------------------------------------------------------------------------
# 程式進入點 (Application Entry Point)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("==========================================================")
    print(" [丞石建築開發] 不動產資訊 Portal 服務正在啟動...")
    print(" 本地訪問地址: http://127.0.0.1:5000")
    print(" 區網訪問地址: http://0.0.0.0:5000")
    print("==========================================================")
    app.run(debug=False, host="0.0.0.0", port=5000)
