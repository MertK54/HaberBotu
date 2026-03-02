import os
import asyncio
import hashlib
import logging
import requests
import feedparser
import psycopg2
import yfinance as yf
from datetime import datetime, timedelta, timezone
from telegram.ext import ApplicationBuilder, ContextTypes
from groq import AsyncGroq

# --- AYARLAR VE KİMLİK BİLGİLERİ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
# Her iki alıcıyı da listeye ekledik
ALICI_LISTESI = ["6415717633", "8693042848"]

ai_client = AsyncGroq(api_key=GROQ_API_KEY)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- VERİTABANI İŞLEMLERİ (SUPABASE / POSTGRESQL) ---
def init_db():
    """Haber hafızası için gerekli tabloyu oluşturur."""
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
    """Haberi kontrol eder, yoksa ekler ve False döner."""
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
    """Sakarya/Türkiye yerel saatini döner."""
    return datetime.now(timezone(timedelta(hours=3)))

def get_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

# --- PİYASA VERİSİ ---
def anlik_piyasa_verisi():
    try:
        # BIST ve Döviz verileri
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
    except Exception as e:
        logging.error(f"Piyasa verisi çekilemedi: {e}")
        return "⚠️ Fiyat verisi şu an alınamıyor."

# --- HABER TARAMA VE BİLDİRİM ---
async def kritik_haber_tara(context: ContextTypes.DEFAULT_TYPE):
    """Kritik haberleri 7/24 anında gönderir."""
    kaynaklar = ["https://tr.investing.com/rss/news_285.rss", "https://tr.investing.com/rss/news_301.rss"]
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for url in kaynaklar:
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).content)
            for entry in feed.entries[:5]:
                baslik = entry.title.strip()
                h_hash = get_hash(baslik)
                
                # Kritik filtreleme
                if any(k in baslik.lower() for k in ["savaş", "nükleer", "çöktü", "faiz kararı", "acil", "crash", "saldırı"]):
                    if not haber_gonderildi_mi(h_hash):
                        resp = await ai_client.chat.completions.create(
                            messages=[{"role": "user", "content": f"Şu haberi 1 cümleyle analiz et: {baslik}"}],
                            model="llama-3.3-70b-versatile"
                        )
                        yorum = resp.choices[0].message.content
                        for cid in ALICI_LISTESI:
                            await context.bot.send_message(chat_id=cid, text=f"🚨 *KRİTİK*\n\n📰 {baslik}\n\n💡 {yorum}", parse_mode="Markdown")
        except: continue

async def saatlik_rapor(context: ContextTypes.DEFAULT_TYPE):
    """Her saat başı (XX:00) rapor gönderir. Gece 00-07 arası susar."""
    simdi = tr_saati()
    if 0 <= simdi.hour < 7:
        logging.info("Gece modu: Saatlik rapor atlanıyor.")
        return

    mesaj = anlik_piyasa_verisi()
    for cid in ALICI_LISTESI:
        await context.bot.send_message(chat_id=cid, text=f"{mesaj}\n\n✅ Saatlik bilgilendirme tamamlandı.", parse_mode="Markdown")

# --- ZAMANLAYICI AYARLARI ---
def setup_jobs(job_queue):
    simdi = tr_saati()
    # Bir sonraki tam saate kalan süreyi hesapla (Örn: 14:23 ise 15:00'a 37 dk var)
    sonraki_saat = (simdi + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    bekleme_suresi = (sonraki_saat - simdi).total_seconds()
    
    # 1. Saat başı raporu hizala
    job_queue.run_repeating(saatlik_rapor, interval=3600, first=bekleme_suresi)
    # 2. Kritik haber taraması (3 dakikada bir)
    job_queue.run_repeating(kritik_haber_tara, interval=180, first=10)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    setup_jobs(app.job_queue)
    
    print(f"🚀 Bot Aktif! Saatlik raporlar her saat başında (:00) gönderilecek.")
    app.run_polling()