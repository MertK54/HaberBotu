import os
import asyncio
import hashlib
import logging
import requests
import feedparser
import psycopg2
import yfinance as yf
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import AsyncGroq

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
ALICI_LISTESI = ["6415717633", "8693042848"]

# Nitter örneği (Bazen nitter.net yavaş olabilir, nitter.cz veya nitter.it denenebilir)
NITTER_BASE = "https://nitter.cz" 

ai_client = AsyncGroq(api_key=GROQ_API_KEY)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- YARDIMCI FONKSİYONLAR ---
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS haber_hafiza (hash TEXT PRIMARY KEY, tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"DB Hatası: {e}")

def yeni_mi(h_hash):
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM haber_hafiza WHERE hash = %s", (h_hash,))
        exists = cur.fetchone()
        if not exists:
            cur.execute("INSERT INTO haber_hafiza (hash) VALUES (%s)", (h_hash,))
            conn.commit()
        cur.close()
        conn.close()
        return exists is None
    except: return False

def tr_saati():
    return datetime.now(timezone(timedelta(hours=3)))

# --- PİYASA MOTORU (% DEĞİŞİM DAHİL) ---
def get_piyasa_verisi():
    try:
        tickers = {
            "BIST 100": "XU100.IS", 
            "USD/TRY": "USDTRY=X", 
            "Altın (Ons)": "GC=F", 
            "Gümüş (Ons)": "SI=F", 
            "Bitcoin": "BTC-USD"
        }
        
        rapor_str = f"📊 *FİNANSAL DURUM* ({tr_saati().strftime('%H:%M')})\n━━━━━━━━━━━━━━━━━━━━\n"
        piyasa_ozeti_ai = "" # AI'ya ham veri göndermek için

        for isim, sembol in tickers.items():
            t = yf.Ticker(sembol)
            hist = t.history(period="2d")
            
            if len(hist) >= 2:
                guncel = hist['Close'].iloc[-1]
                onceki = hist['Close'].iloc[-2]
                degisim = ((guncel - onceki) / onceki) * 100
                emoji = "🔺" if degisim > 0 else "🔻"
                
                rapor_str += f"{emoji} *{isim}:* {guncel:,.2f} (%{degisim:+.2f})\n"
                piyasa_ozeti_ai += f"{isim}: {guncel:.2f} (Degisim: %{degisim:.2f}), "
            else:
                last = t.fast_info.last_price
                rapor_str += f"🔹 *{isim}:* {last:,.2f}\n"
                piyasa_ozeti_ai += f"{isim}: {last:.2f}, "

        return rapor_str, piyasa_ozeti_ai
    except Exception as e:
        logging.error(f"Veri hatası: {e}")
        return "⚠️ Piyasa verisi alınamadı.", ""

# --- HABER VE ANALİZ ---
async def bulten_hazirla_ve_gonder(bot, hedefler):
    kaynaklar = [
        f"{NITTER_BASE}/haskologlu/rss",
        f"{NITTER_BASE}/bpthaber/rss",
        "https://www.ekonomim.com/rss",
        "https://www.bloomberght.com/rss",
        "https://www.reuters.com/arc/outboundfeeds/rss/category/world/?outputType=xml"
    ]
    
    toplanan_haberler = []
    headers = {'User-Agent': 'Mozilla/5.0'}

    for url in kaynaklar:
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).content)
            for entry in feed.entries[:10]:
                baslik = entry.title.strip()
                if yeni_mi(hashlib.sha256(baslik.encode()).hexdigest()):
                    toplanan_haberler.append(baslik)
        except: continue

    if not toplanan_haberler: return

    piyasa_text, ai_verisi = get_piyasa_verisi()
    haber_metni = "\n".join([f"- {h}" for h in toplanan_haberler])

    prompt = (f"Sen uzman bir piyasa analistisin. Verileri ve haberleri harmanla.\n\n"
              f"Piyasalar: {ai_verisi}\n\n"
              f"Gelişmeler:\n{haber_metni}\n\n"
              f"Görev: Bu haberlerin BIST100, Altın, Gümüş ve Bitcoin üzerindeki etkisini "
              f"madde madde açıkla. % değişimleri de yorumuna kat. Profesyonel ol.")

    try:
        resp = await ai_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile"
        )
        final_mesaj = f"{piyasa_text}\n\n🔍 *STRATEJİK ANALİZ*\n\n{resp.choices[0].message.content}"
        
        for cid in hedefler:
            await bot.send_message(chat_id=cid, text=final_mesaj, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"AI Hatası: {e}")

# --- KOMUT VE ÇALIŞTIRMA ---
async def test_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Analiz hazırlanıyor, bekleyin...")
    await bulten_hazirla_ve_gonder(context.bot, [str(update.effective_chat.id)])

async def saatlik_is(context: ContextTypes.DEFAULT_TYPE):
    if 0 <= tr_saati().hour < 7: return
    await bulten_hazirla_ve_gonder(context.bot, ALICI_LISTESI)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("test", test_komutu))
    
    app.job_queue.run_repeating(saatlik_is, interval=3600, first=10)
    
    print("🚀 Bot yayında!")
    app.run_polling()