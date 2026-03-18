#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import json, os, threading, time, sqlite3, urllib.request, urllib.parse

PORT = int(os.environ.get("PORT", 8766))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DB_PATH = os.path.join(os.path.dirname(__file__), "gs.db")

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

def gs_haberleri_cek():
    """RSS ile GS haberlerini çek"""
    haberler = []
    kaynaklar = [
        "https://www.galatasaray.org/rss/haberler",
        "https://feeds.feedburner.com/ntvspor-galatasaray",
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
            haberler.extend([t.strip() for t in titles if t.strip()])
        except:
            pass
    return haberler[:10] if haberler else ["Galatasaray haberleri yüklenemedi"]

def claude_tweet_onerisi(fikirler, haberler):
    if not ANTHROPIC_KEY:
        return ["API key eksik"]
    try:
        sistem = """Sen Galatasaray taraftarı, tutkulu ve etkileşimi yüksek tweet içerikleri üreten bir sosyal medya uzmanısın.
Galatasaray'ı her zaman savun, rakipleri nazikçe eleştir.
Tweet'ler Türkçe, kısa, güçlü, emoji kullanan ve viral olabilecek tarzda olsun.
Her tweet 280 karakteri geçmesin."""

        kullanici = f"""Bugünkü GS haberleri:
{chr(10).join(['- ' + h for h in haberler])}

Benim fikirlerim ve görüşlerim:
{fikirler if fikirler else 'Yok, sadece haberlere göre üret'}

Bunları harmanlayarak 10 farklı tweet önerisi üret. 
Her birini numara ile listele (1. 2. vb).
Çeşitli tarzlarda olsun: ateşli, analitik, mizahi, motivasyonel."""

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

def zamanlayici():
    while True:
        now = turkey_now()
        # Her gün saat 09:00'da gönder
        if now.hour == 9 and now.minute == 0:
            gunluk_oneri_gonder()
            time.sleep(61)
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
