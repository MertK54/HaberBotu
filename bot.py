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
yf.set_tz_cache_location("/tmp/yf_cache")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- KRİTİK HABER HAFIZASI (tekrar bildirimi önler) ---
gonderilen_haberler: set = set()

# --- KRİTİK ANAHTAR KELİMELER ---
# Bu seviyedeki haberler anında, tek tek ve AI yorumuyla gönderilir.
KRITIK_KELIMELER = [

    # 🔴 JEOPOLİTİK / SAVAŞ / GÜVENLİK
    "war", "warfare", "invasion", "nuclear", "missile strike", "airstrike",
    "military attack", "troops deployed", "ceasefire collapsed", "coup",
    "terrorist attack", "assassination", "nato article 5", "world war",
    "savaş", "işgal", "nükleer", "füze saldırısı", "hava saldırısı",
    "askeri operasyon", "darbe", "suikast", "ateşkes bozuldu", "kara harekatı",
    "terör saldırısı", "nato madde 5", "seferberlik",

    # 🔴 GLOBAl EKONOMİ / FİNANSAL KRİZ
    "market crash", "flash crash", "circuit breaker", "market halt",
    "black swan", "financial crisis", "bank run", "bank collapse",
    "sovereign default", "debt crisis", "emergency rate", "fed emergency",
    "recession confirmed", "depression", "hyperinflation",
    "piyasa çöküşü", "borsa çöktü", "finansal kriz", "banka iflası",
    "iflas", "temerrüt", "acil faiz", "ekonomik kriz", "hiperenflasyon",
    "borç krizi", "stagflasyon",

    # 🔴 BORSA / EMTİA ANİ SERT HAREKETLER
    "bist halted", "circuit breaker bist", "s&p 500 crash", "dow crash",
    "nasdaq crash", "gold spike", "silver spike", "oil crash", "oil surge",
    "bitcoin crash", "bitcoin halving", "crypto crash", "crypto ban",
    "altın sert yükseldi", "gümüş sert yükseldi", "petrol çöktü",
    "bist devre kesici", "dolar sert yükseldi", "dolar rekor",
    "bitcoin çöktü", "kripto yasaklandı",

    # 🔴 TÜRKİYE KRİTİK
    "türkiye savaş", "türkiye operasyon", "tcmb acil", "merkez bankası acil",
    "erdoğan istifa", "hükümet istifa", "olağanüstü hal", "sıkıyönetim",
    "türkiye temerrüt", "türkiye iflas", "lira çöküşü", "dolar tl rekor",
    "turkey invasion", "turkey military", "turkey default", "lira collapse",

    # 🔴 AMERİKA / TRUMP KRİTİK
    "trump impeachment", "trump arrested", "us default", "us debt ceiling",
    "government shutdown emergency", "fed chair fired", "powell fired",
    "trump nükleer", "trump savaş", "abd temerrüt",
]

# --- ÖNEMLİ ANAHTAR KELİMELER ---
# Bu seviyedeki haberler gruplu mesajla iletilir.
ONEMLI_KELIMELER = [

    # 🟡 MERKEZ BANKALARI / FAİZ POLİTİKASI
    "fed rate", "rate cut", "rate hike", "fomc", "powell", "fed decision",
    "ecb rate", "lagarde", "bank of england", "interest rate decision",
    "tcmb faiz", "merkez bankası faiz", "faiz kararı", "para politikası",
    "enflasyon raporu", "cpi data", "pce inflation",

    # 🟡 AMERİKAN EKONOMİSİ / POLİTİKASI
    "trump tariff", "trump sanctions", "trump trade", "us gdp", "us cpi",
    "us jobs report", "nonfarm payroll", "us treasury", "bond yield",
    "dollar index", "dxy", "us recession", "fed minutes",
    "trump gümrük", "trump yaptırım", "abd gdp", "abd enflasyon",

    # 🟡 GLOBAL SİYASET / JEOPOLİTİK
    "ukraine", "russia sanctions", "china taiwan", "iran nuclear",
    "middle east", "israel gaza", "opec decision", "oil embargo",
    "g7 summit", "g20 summit", "imf warning", "world bank",
    "ukrayna", "rusya yaptırım", "çin tayvan", "iran nükleer",
    "orta doğu", "opec karar", "petrol ambargosu", "imf uyarı",

    # 🟡 TÜRKİYE EKONOMİ / SİYASET
    "erdoğan", "türkiye enflasyon", "türkiye faiz", "tcmb",
    "bist100", "bist 100", "türkiye büyüme", "türkiye gdp",
    "türkiye döviz", "türkiye cari açık", "türkiye bütçe",
    "hazine borçlanma", "eurobond türkiye", "s&p türkiye", "moody's türkiye",
    "turkey inflation", "turkey gdp", "turkey rating",

    # 🟡 BORSA / EMTİA / KRİPTO
    "s&p 500", "nasdaq", "dow jones", "bist", "dax", "ftse",
    "gold price", "silver price", "altın fiyat", "gümüş fiyat",
    "oil price", "brent crude", "petrol fiyat",
    "bitcoin", "ethereum", "btc", "crypto market", "kripto",
    "dolar tl", "euro tl", "usdtry",

    # 🟡 ŞİRKET / SEKTÖR HABERLERİ
    "fed balance sheet", "bank earnings", "jpmorgan", "goldman sachs",
    "apple earnings", "nvidia earnings", "tesla recall",
    "semiconductor shortage", "chip ban", "tech layoffs",
]

# --- HAYALET SUNUCU ---
def run_dummy_server():
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
        tickers = {
            "BIST":  "XU100.IS",
            "ALTIN": "GC=F",
            "GUMUS": "SI=F",
            "BTC":   "BTC-USD",
            "USD":   "USDTRY=X",
        }

        fiyatlar = {}
        onceki   = {}

        for key, sembol in tickers.items():
            t = yf.Ticker(sembol)
            fi = t.fast_info          # gerçek zamanlı anlık veri
            fiyatlar[key] = fi.last_price
            onceki[key]   = fi.previous_close

        zaman = datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=3))
        ).strftime('%d.%m %H:%M')

        usd_anlik  = fiyatlar["USD"]
        usd_onceki = onceki["USD"]

        def fmt(key, unit="", is_gram=False):
            val   = fiyatlar[key]
            p_val = onceki[key]
            if is_gram:
                val   = (val   / 31.1035) * usd_anlik
                p_val = (p_val / 31.1035) * usd_onceki
            diff = ((val - p_val) / p_val) * 100
            icon = "📈" if diff >= 0 else "📉"
            return f"{icon} {key}: **{val:,.2f} {unit}** ({diff:+.2f}%)"

        return (
            f"📊 **ANLIK PİYASA ({zaman})**\n"
            f"{fmt('BIST')}\n"
            f"{fmt('ALTIN', 'TL', True)}\n"
            f"{fmt('GUMUS', 'TL', True)}\n"
            f"{fmt('BTC', '$')}\n"
            f"{fmt('USD', 'TL')}\n---"
        )
    except Exception as e:
        logging.error(f"Piyasa verisi hatası: {e}")
        return "⚠️ Fiyat verisi çekilemedi."

# --- HABER ÖNEMİNİ DEĞERLENDİR ---
def haber_seviyesi(baslik: str) -> str:
    """
    Haber başlığını tarar ve önem seviyesini döner.
    'kritik', 'onemli' veya None döner.
    """
    baslik_lower = baslik.lower()
    for kelime in KRITIK_KELIMELER:
        if kelime in baslik_lower:
            return "kritik"
    for kelime in ONEMLI_KELIMELER:
        if kelime in baslik_lower:
            return "onemli"
    return None

# --- ORTAK MESAJ GÖNDERME ---
async def mesaj_gonder(bot, hedefler: list, metin: str):
    for cid in hedefler:
        try:
            await bot.send_message(chat_id=cid, text=metin, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Mesaj gönderilemedi ({cid}): {e}")

# --- HABER TARAMA ÇEKİRDEĞİ ---
async def haber_tara_cekirdek(bot, hedefler: list, gece_modu: bool = True):
    if gece_modu:
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).hour
        if 0 <= saat < 7:
            return

    kaynaklar = [
        "https://tr.investing.com/rss/news_285.rss",
        "https://tr.investing.com/rss/news_301.rss",
        "https://tr.investing.com/rss/news.rss",
        "https://tr.investing.com/rss/market_overview.rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]
    headers = {'User-Agent': 'Mozilla/5.0'}
    yeni_kritik = []
    yeni_onemli = []

    for url in kaynaklar:
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).content)
            for entry in feed.entries[:20]:
                baslik = entry.title.strip()
                if baslik in gonderilen_haberler:
                    continue
                seviye = haber_seviyesi(baslik)
                if seviye == "kritik":
                    yeni_kritik.append(baslik)
                    gonderilen_haberler.add(baslik)
                elif seviye == "onemli":
                    yeni_onemli.append(baslik)
                    gonderilen_haberler.add(baslik)
        except:
            continue

    # Kritik haberleri tek tek AI yorumuyla gönder
    for baslik in yeni_kritik:
        try:
            resp = await ai_client.chat.completions.create(
                messages=[{"role": "user", "content": (
                    f"Bu haber başlığını 1-2 cümleyle Türkçe yorumla, "
                    f"piyasaya (borsa, dolar, altın) olası etkisini belirt: '{baslik}'"
                )}],
                model="llama-3.3-70b-versatile",
                temperature=0.2
            )
            yorum = resp.choices[0].message.content.strip()
        except:
            yorum = "⚠️ AI yorum yapamadı."
        await mesaj_gonder(bot, hedefler, f"🚨 *KRİTİK UYARI*\n\n📰 {baslik}\n\n💬 {yorum}")
        await asyncio.sleep(1)

    # Önemli haberleri gruplu gönder
    if yeni_onemli:
        ozet = "\n".join([f"• {b}" for b in yeni_onemli[:5]])
        await mesaj_gonder(bot, hedefler, f"📌 *YENİ ÖNEMLİ HABERLER*\n\n{ozet}")

    if not yeni_kritik and not yeni_onemli:
        logging.info("Haber taraması: yeni kritik/önemli haber bulunamadı.")

    # Hafıza temizliği
    if len(gonderilen_haberler) > 500:
        liste = list(gonderilen_haberler)
        gonderilen_haberler.clear()
        gonderilen_haberler.update(liste[-250:])

# --- JOB QUEUE SARMALAYICISI ---
async def haber_tara(context: ContextTypes.DEFAULT_TYPE):
    await haber_tara_cekirdek(context.bot, ALICI_LISTESI, gece_modu=True)

# --- STRATEJİK ANALİZ MOTORU ---
async def ai_stratejik_analiz(metin):
    if not metin or len(metin) < 20:
        return "📌 Şu an için kritik bir gelişme saptanmadı."
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
    except:
        return "⚠️ Analiz motoru meşgul."

# --- RAPOR ÇEKİRDEĞİ ---
async def rapor_gonder_cekirdek(bot, hedefler: list, gece_modu: bool = True):
    if gece_modu:
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
        except:
            continue
    analiz = await ai_stratejik_analiz(raw_news)
    await mesaj_gonder(bot, hedefler, f"{fiyatlar}\n\n{analiz}")

# --- JOB QUEUE SARMALAYICISI ---
async def rapor_gonder(context: ContextTypes.DEFAULT_TYPE):
    await rapor_gonder_cekirdek(context.bot, ALICI_LISTESI, gece_modu=True)

# --- KOMUTLAR ---
async def test_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Komutu yazan kişinin chat_id'sine gönder, gece modunu atla
    chat_id = str(update.effective_chat.id)
    msg = await update.message.reply_text("⏳ Anlık fiyatlar ve haberler çekiliyor...")
    await rapor_gonder_cekirdek(context.bot, [chat_id], gece_modu=False)
    await haber_tara_cekirdek(context.bot, [chat_id], gece_modu=False)
    await msg.edit_text("✅ Test tamamlandı.")

async def tara_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    msg = await update.message.reply_text("🔍 Kritik haber taraması başlatılıyor...")
    await haber_tara_cekirdek(context.bot, [chat_id], gece_modu=False)
    await msg.edit_text("✅ Tarama tamamlandı.")

# --- ANA ÇALIŞTIRICI ---
if __name__ == '__main__':
    # 1. Hayalet sunucuyu arka planda başlat
    threading.Thread(target=run_dummy_server, daemon=True).start()

    # 2. Telegram Botunu kur
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 3. İşleri tanımla
    app.job_queue.run_repeating(rapor_gonder, interval=3600, first=10)       # Saatlik tam rapor
    app.job_queue.run_repeating(haber_tara, interval=900, first=5)           # 15 dk'da bir kritik tarama

    # 4. Komutları tanımla
    app.add_handler(CommandHandler("test", test_komutu))
    app.add_handler(CommandHandler("tara", tara_komutu))

    print("🤖 Bot Aktif! Saatlik rapor + 15dk kritik haber taraması çalışıyor.")
    app.run_polling()