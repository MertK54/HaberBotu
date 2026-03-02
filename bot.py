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
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from groq import AsyncGroq

# --- AYARLAR VE KİMLİK BİLGİLERİ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
ALICI_LISTESI = ["6415717633", "8693042848"]

ai_client = AsyncGroq(api_key=GROQ_API_KEY)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- VERİTABANI İŞLEMLERİ ---
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS haber_hafiza (
                hash TEXT PRIMARY KEY, 
                tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logging.info("✅ Supabase bağlantısı kuruldu.")
    except Exception as e:
        logging.error(f"❌ DB Hatası: {e}")

def haber_gonderildi_mi(h_hash):
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
        return exists is not None
    except Exception as e:
        logging.error(f"⚠️ DB Sorgu Hatası: {e}")
        return False

# --- YARDIMCI FONKSİYONLAR ---
def tr_saati():
    return datetime.now(timezone(timedelta(hours=3)))

def get_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

# --- PİYASA VE HABER MOTORU ---
def anlik_piyasa_verisi():
    try:
        tickers = {"BIST": "XU100.IS", "ALTIN": "GC=F", "BTC": "BTC-USD", "USD": "USDTRY=X"}
        fiyatlar = {}
        for key, sembol in tickers.items():
            t = yf.Ticker(sembol)
            fiyatlar[key] = t.fast_info.last_price
        
        zaman = tr_saati().strftime('%H:%M')
        return (f"📊 *GÜNCEL PİYASA ({zaman})*\n"
                f"🔹 BIST 100: {fiyatlar['BIST']:,.0f}\n"
                f"🔹 USD/TRY: {fiyatlar['USD']:.2f} TL\n"
                f"🔹 Altın (Ons): {fiyatlar['ALTIN']:,.0f} $\n"
                f"🔹 Bitcoin: {fiyatlar['BTC']:,.0f} $")
    except:
        return "⚠️ Fiyat verisi şu an alınamıyor."

async def haber_tara_ve_gonder(bot, hedefler, zorla=False):
    """Haberleri tarar. zorla=True ise hafızaya bakmaksızın en son haberleri özetler."""
    kaynaklar = ["https://tr.investing.com/rss/news_285.rss", "https://tr.investing.com/rss/news_301.rss"]
    headers = {'User-Agent': 'Mozilla/5.0'}
    bulunan_haberler = []

    for url in kaynaklar:
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).content)
            for entry in feed.entries[:5]:
                baslik = entry.title.strip()
                h_hash = get_hash(baslik)
                
                # Eğer normal taramaysa (3 dk'da bir olan) hafızayı kontrol et
                if not zorla:
                    if any(k in baslik.lower() for k in ["savaş", "nükleer", "çöktü", "faiz", "acil", "saldırı"]):
                        if not haber_gonderildi_mi(h_hash):
                            bulunan_haberler.append(baslik)
                else:
                    # /test komutu için en son 3 haberi al
                    bulunan_haberler.append(baslik)
                    if len(bulunan_haberler) >= 3: break
        except: continue

    if bulunan_haberler:
        haber_metni = "\n".join([f"• {h}" for h in bulunan_haberler[:5]])
        try:
            resp = await ai_client.chat.completions.create(
                messages=[{"role": "user", "content": f"Şu haberleri kısaca özetle ve piyasa etkisini söyle: {haber_metni}"}],
                model="llama-3.3-70b-versatile"
            )
            analiz = resp.choices[0].message.content
        except:
            analiz = "⚠️ AI analiz yapamadı."
        
        for cid in hedefler:
            await bot.send_message(chat_id=cid, text=f"📰 *GÜNCEL GELİŞMELER*\n\n{analiz}", parse_mode="Markdown")

# --- KOMUTLAR VE ZAMANLI GÖREVLER ---
async def test_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saate bakmaksızın anlık rapor ve haber getirir."""
    await update.message.reply_text("⏳ Veriler çekiliyor, lütfen bekleyin...")
    
    # 1. Piyasa Verisi
    piyasa = anlik_piyasa_verisi()
    await update.message.reply_text(piyasa, parse_mode="Markdown")
    
    # 2. Haber Taraması (Zorunlu modda)
    await haber_tara_ve_gonder(context.bot, [str(update.effective_chat.id)], zorla=True)

async def kritik_haber_tara_job(context: ContextTypes.DEFAULT_TYPE):
    """Otomatik 3 dakikalık tarama."""
    await haber_tara_ve_gonder(context.bot, ALICI_LISTESI, zorla=False)

async def saatlik_rapor_job(context: ContextTypes.DEFAULT_TYPE):
    """Otomatik saatlik rapor (Gece 00-07 arası çalışmaz)."""
    simdi = tr_saati()
    if 0 <= simdi.hour < 7:
        return
    
    piyasa = anlik_piyasa_verisi()
    for cid in ALICI_LISTESI:
        await context.bot.send_message(chat_id=cid, text=f"{piyasa}\n\n✅ Saatlik bilgilendirme.", parse_mode="Markdown")

# --- BAŞLATICI ---
def setup_jobs(job_queue):
    if job_queue is None:
        logging.error("❌ JobQueue başlatılamadı! requirements.txt dosyasını kontrol edin.")
        return
        
    simdi = tr_saati()
    sonraki_saat = (simdi + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    bekleme = (sonraki_saat - simdi).total_seconds()
    
    job_queue.run_repeating(saatlik_rapor_job, interval=3600, first=bekleme)
    job_queue.run_repeating(kritik_haber_tara_job, interval=180, first=10)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Komutları Tanımla
    app.add_handler(CommandHandler("test", test_komutu))
    
    # Zamanlayıcıyı Başlat
    setup_jobs(app.job_queue)
    
    print("🚀 Bot Aktif! /test komutu ve otomatik raporlar hazır.")
    app.run_polling()