# AI Finance Intelligence

AI destekli finans istihbarat platformu — **Python + FastAPI + web dashboard**.
Ucretsiz veri (yfinance + RSS) ve **Claude API** ile calisir.

> **Uyari:** Uretilen tum ciktilar bilgilendirme amaclidir, **yatirim tavsiyesi degildir.**

## Ozellikler

- **Piyasa Analizi + AI Sinyal** — fiyat, teknik gostergeler (RSI, MACD, SMA/EMA, volatilite) ve Claude tabanli AL/SAT/TUT sinyali.
- **Haber + Duygu Istihbarati** — RSS ile finansal haber + Claude ile ozet ve sentiment.
- **Portfoy + Risk Analizi** — pozisyon takibi, kantitatif risk (volatilite, max drawdown, VaR, kar/zarar) ve Claude tabanli kisisel oneri.

"Degraded mode": `ANTHROPIC_API_KEY` yoksa gosterge/haber/risk metrikleri yine calisir; yalnizca AI yorumlari devre disi kalir.

## Kurulum

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ANTHROPIC_API_KEY doldur (istege bagli)
python run.py
```

- Dashboard: http://127.0.0.1:8000
- Saglik: http://127.0.0.1:8000/health
- API dokumani: http://127.0.0.1:8000/docs

## API

| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/api/market/{symbol}` | Fiyat + gosterge + AI sinyal |
| GET | `/api/news?query=` | Haber + AI ozet/sentiment |
| GET | `/api/portfolio` | Pozisyonlari listele |
| POST | `/api/portfolio` | Pozisyon ekle |
| DELETE | `/api/portfolio/{id}` | Pozisyon sil |
| GET | `/api/portfolio/risk` | Risk raporu + AI oneri |

## Mimari

- `app/core` — config (pydantic-settings), cache, hata yonetimi, DI
- `app/providers` — **pluggable** veri kaynaklari (yfinance, RSS); ucretli kaynak eklemek yeni bir provider dosyasi yeterli
- `app/services` — is mantigi (indicators, ai, market, news, portfolio)
- `app/api/routes` — FastAPI router'lari
- `app/db` — SQLite (SQLModel)
- `web` — dashboard

## Test

```bash
pytest -q
```

Testlerde Claude ve veri saglayicilari mock'lanir (ag erisimi yok).

## Yol Haritasi

Watchlist/alerts, WebSocket canli fiyat, backtesting, auth, Docker/Render deploy, ucretli veri saglayicilari (Alpha Vantage/Polygon).
