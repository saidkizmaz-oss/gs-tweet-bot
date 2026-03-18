#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import json, os, threading, time, sqlite3, urllib.request, urllib.parse, asyncio

PORT = int(os.environ.get("PORT", 8766))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TW_USERNAME = os.environ.get("TW_USERNAME", "")
TW_PASSWORD = os.environ.get("TW_PASSWORD", "")
DB_PATH = os.path.join(os.path.dirname(__file__), "gs.db")

# twscrape kurulu mu?
try:
    import twscrape
    TWSCRAPE_OK = True
except ImportError:
    TWSCRAPE_OK = False

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS oneriler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        icerik TEXT, kaynak TEXT, durum TEXT DEFAULT 'BEKLIYOR',
        tarih TEXT, saat TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS fikirler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fikir TEXT, tarih TEXT)""")
    con.commit(); con.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def turkey_now():
    return datetime.utcnow() + timedelta(hours=3)

def telegram_gonder(mesaj):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {mesaj[:100]}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram hata: {e}")

async def twitter_tara_async():
    """twscrape ile Twitter'dan GS tweetlerini çek"""
    if not TWSCRAPE_OK or not TW_USERNAME or not TW_PASSWORD:
        return []
    try:
        from twscrape import API
        api = API()
        await api.pool.add_account(TW_USERNAME, TW_PASSWORD, "", "")
        await api.pool.login_all()
        tweetler = []
        async for tweet in api.search("Galatasaray lang:tr", limit=30):
            if tweet.likeCount + tweet.retweetCount > 50:
                tweetler.append({
                    "text": tweet.rawContent[:200],
                    "likes": tweet.likeCount,
                    "rt": tweet.retweetCount
                })
        tweetler.sort(key=lambda x: x["likes"] + x["rt"]*2, reverse=True)
        return tweetler[:10]
    except Exception as e:
        print(f"Twitter tarama hata: {e}")
        return []

def twitter_tara():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(twitter_tara_async())
    except:
        return []

def gs_haberleri_cek():
    """RSS + Twitter ile GS içeriklerini çek"""
    haberler = []

    # RSS - çoklu kaynak
    kaynaklar = [
        "https://www.galatasaray.org/rss/haberler",
        "https://feeds.feedburner.com/ntvspor-galatasaray",
        "https://www.fanatik.com.tr/rss/galatasaray",
        "https://www.milliyet.com.tr/rss/rssNew/spor-galatasaray-haberleri-rss.xml",
        "https://www.sporx.com/rss/galatasaray",
    ]
    for url in kaynaklar:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = urllib.request.urlopen(req, timeout=8)
            content = res.read().decode("utf-8", errors="ignore")
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', content)[:5]
            if not titles:
                titles = re.findall(r'<title>(.*?)</title>', content)[1:6]
            titles = [t.strip() for t in titles if t.strip() and len(t.strip()) > 10]
            haberler.extend(titles)
        except:
            pass

    # Twitter
    tweetler = twitter_tara()
    for t in tweetler:
        haberler.append(f"[Twitter {t['likes']}❤️ {t['rt']}🔁] {t['text']}")

    return haberler[:20] if haberler else ["Galatasaray haberleri yüklenemedi"]

def claude_tweet_onerisi(fikirler, haberler):
    if not ANTHROPIC_KEY:
        return ["API key eksik"]
    try:
        sistem = """Sen Galatasaray Twitter fenomeni için içerik üreten bir uzmansın. Milyonlarca takipçisi olan GS hesaplarının tarzını biliyorsun.

KURALLAR:
- Güncel haberlerden SOMUT detaylar kullan (oyuncu adı, skor, transfer adı, vb.)
- Genel "Galatasaray en iyisi" klişelerinden KAÇIN
- Her tweet farklı bir his uyandırsın: öfke, gurur, dalga geçme, analiz, hype
- Rakip takımlara (FB, BJK, TM) zaman zaman ince göndermeler yap
- Gündem olan konuya doğrudan gir, soyut kalma
- Emoji kullan ama abartma (max 3-4)
- 280 karakter sınırını geçme
- Türk Twitter dilini kullan: samimi, sokak dili, bazen caps lock vurgu"""

        kullanici = f"""Bugünkü GS gündemi:
{chr(10).join(['- ' + h for h in haberler])}

{'Benim görüşüm/isteğim: ' + fikirler if fikirler else ''}

Bu gündemdeki SOMUT konulara dayalı 10 farklı tweet önerisi yaz.
Haberlerdeki isim, skor, olay detaylarını direkt kullan — soyut kalma.
Her birini numara ile listele (1. 2. vb)."""

        data = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 2000,
            "system": sistem,
            "messages": [{"role": "user", "content": kullanici}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01"
            }
        )
        res = urllib.request.urlopen(req, timeout=30)
        result = json.loads(res.read())
        text = result["content"][0]["text"]

        import re
        tweets = re.findall(r'\d+\.\s*(.+?)(?=\n\d+\.|\Z)', text, re.DOTALL)
        tweets = [t.strip() for t in tweets if t.strip()]
        return tweets[:10] if tweets else [text]
    except Exception as e:
        print(f"Claude hata: {e}")
        return [f"Hata: {e}"]

def gunluk_oneri_gonder(fikirler=""):
    print(f"[{turkey_now().strftime('%H:%M')}] Günlük öneriler hazırlanıyor...")
    haberler = gs_haberleri_cek()
    tweets = claude_tweet_onerisi(fikirler, haberler)

    now = turkey_now()
    con = get_db()
    for t in tweets:
        con.execute("INSERT INTO oneriler (icerik, kaynak, durum, tarih, saat) VALUES (?,?,?,?,?)",
            (t, "gunluk", "BEKLIYOR", now.strftime("%Y-%m-%d"), now.strftime("%H:%M")))
    con.commit(); con.close()

    mesaj = f"🦁 <b>GÜNLÜK GS TWEET ÖNERİLERİ</b>\n{now.strftime('%d.%m.%Y')}\n\n"
    for i, t in enumerate(tweets, 1):
        mesaj += f"<b>{i}.</b> {t}\n\n"
    mesaj += "✅ Beğendiklerini manuel olarak at!"
    telegram_gonder(mesaj)
    print(f"[{turkey_now().strftime('%H:%M')}] {len(tweets)} öneri Telegram'a gönderildi.")

ONERI_SAATLERI = {8, 10, 12, 14, 16, 18, 20, 22, 0}  # 08:00'den 24:00'a her 2 saatte bir

def zamanlayici():
    son_gonderilen = -1
    while True:
        now = turkey_now()
        if now.hour in ONERI_SAATLERI and now.minute == 0 and now.hour != son_gonderilen:
            son_gonderilen = now.hour
            gunluk_oneri_gonder()
        time.sleep(30)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        path = urlparse(self.path).path

        if path == "/api/uret":
            fikir = body.get("fikir", "")
            if fikir:
                con = get_db()
                con.execute("INSERT INTO fikirler (fikir, tarih) VALUES (?,?)",
                    (fikir, turkey_now().strftime("%Y-%m-%d %H:%M")))
                con.commit(); con.close()
            threading.Thread(target=gunluk_oneri_gonder, args=(fikir,), daemon=True).start()
            self.send_json({"ok": True, "mesaj": "Öneriler hazırlanıyor, Telegram'a gelecek..."})

        else:
            self.send_response(404); self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self.serve_file()
        elif path == "/api/oneriler":
            con = get_db()
            rows = con.execute("SELECT * FROM oneriler ORDER BY id DESC LIMIT 50").fetchall()
            con.close()
            cols = ["id","icerik","kaynak","durum","tarih","saat"]
            self.send_json({"oneriler": [dict(zip(cols,r)) for r in rows]})
        else:
            self.send_response(404); self.end_headers()

    def serve_file(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(base,"static","index.html"), os.path.join(base,"index.html")]:
            if os.path.exists(p):
                with open(p,"rb") as f: content=f.read()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",len(content))
                self.end_headers()
                self.wfile.write(content)
                return
        self.send_response(404); self.end_headers()

if __name__ == "__main__":
    init_db()
    threading.Thread(target=zamanlayici, daemon=True).start()
    print(f"GS Bot başladı → http://0.0.0.0:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
