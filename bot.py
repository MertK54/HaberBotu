import os
import asyncio
import logging
import requests
import feedparser
import yfinance as yf
from datetime import datetime, timedelta, timezone
from dateutil import parser
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import AsyncGroq
import http.server
import socketserver
import threading

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ALICI_LISTESI = ["6415717633", "8693042848"] 

ai_client = AsyncGroq(api_key=GROQ_API_KEY)
yf.set_tz_cache_location("C:/temp/yf_cache")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- HAYALET SUNUCU (RENDER İÇİN) ---
def run_dummy_server():
    """Render'ın 'Port' beklentisini karşılayan hayalet web sunucusu."""
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            logging.info(f"Port {port} üzerinde hayalet sunucu aktif.")
            httpd.serve_forever()
    except Exception as e:
        logging.error(f"Hayalet sunucu başlatılamadı: {e}")

# --- ANLIK PİYASA VERİSİ ---
def anlik_piyasa_verisi():
    try:
        tickers = {"BIST": "XU100.IS", "ALTIN": "GC=F", "GUMUS": "SI=F", "BTC": "BTC-USD", "USD": "USDTRY=X"}
        data = yf.download(list(tickers.values()), period="2d", interval="1d", progress=False, threads=False)['Close'].ffill()
        curr, prev = data.iloc[-1], data.iloc[-2]
        zaman = data.index[-1].strftime('%d.%m %H:%M')

        def fmt(key, unit="", is_gram=False):
            val = curr[tickers[key]]
            p_val = prev[tickers[key]]
            if is_gram:
                val = (val / 31.1035) * curr[tickers["USD"]]
                p_val = (p_val / 31.1035) * prev[tickers["USD"]]
            diff = ((val - p_val) / p_val) * 100
            icon = "📈" if diff >= 0 else "📉"
            return f"{icon} {key}: **{val:,.2f} {unit}** ({diff:+.2f}%)"

        return (f"📊 **PİYASA RAPORU ({zaman})**\n"
                f"{fmt('BIST')}\n{fmt('ALTIN', 'TL', True)}\n{fmt('GUMUS', 'TL', True)}\n"
                f"{fmt('BTC', '$')}\n{fmt('USD', 'TL')}\n---")
    except: return "⚠️ Fiyat verisi çekilemedi."

# --- STRATEJİK ANALİZ MOTORU ---
async def ai_stratejik_analiz(metin):
    if not metin or len(metin) < 20: return "📌 Şu an için kritik bir gelişme saptanmadı."
    
    prompt = f"""Sen kıdemli bir Finansal Stratejistsin. Haberleri analiz et.
    KURALLAR:
    1. Haberleri 'Önem Derecesine' göre sırala (🔴 Kritik, 🟡 Önemli).
    2. SADECE: Trump, Fed, Enflasyon, Faiz, Orta Doğu ve Rusya/Çin gerilimlerini al.
    3. Sosyal haberleri ASLA alma.
    4. YORUM: Haberi ver ve bunun piyasaya etkisini TEK BİR kısa cümlede açıkla.
    5. Paragraf kullanma, madde madde yaz.
    Haberler: {metin}"""

    try:
        response = await ai_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.2
        )
        return response.choices[0].message.content
    except: return "⚠️ Analiz motoru meşgul."

# --- ANA DÖNGÜ VE GECE MODU ---
async def rapor_gonder(context: ContextTypes.DEFAULT_TYPE):
    saat = (datetime.now(timezone.utc) + timedelta(hours=3)).hour
    if 0 <= saat < 8:
        return

    fiyatlar = anlik_piyasa_verisi()
    raw_news = ""
    kaynaklar = [
        "https://tr.investing.com/rss/news_285.rss", "https://tr.investing.com/rss/news_301.rss",
        "https://tr.investing.com/rss/news.rss", "https://tr.investing.com/rss/market_overview.rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in kaynaklar:
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).content)
            for entry in feed.entries[:15]:
                raw_news += f"{entry.title}. "
        except: continue

    analiz = await ai_stratejik_analiz(raw_news)
    final_mesaj = f"{fiyatlar}\n\n{analiz}"
    
    for cid in ALICI_LISTESI:
        try: await context.bot.send_message(chat_id=cid, text=final_mesaj, parse_mode="Markdown")
        except: continue

async def test_komutu(update, context):
    await update.message.reply_text("🚀 Stratejik rapor hazırlanıyor...")
    await rapor_gonder(context)

# --- ANA ÇALIŞTIRICI ---
if __name__ == '__main__':
    # 1. Hayalet sunucuyu arka planda (Thread) başlat
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # 2. Telegram Botunu kur
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # 3. İşleri ve Komutları tanımla
    app.job_queue.run_repeating(rapor_gonder, interval=3600, first=5)
    app.add_handler(CommandHandler("test", test_komutu))
    
    # 4. Botu başlat (Bu satır bloklayıcıdır, en sonda kalmalı)
    print("🤖 Bot ve Hayalet Sunucu Aktif!")
    app.run_polling()