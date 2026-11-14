# LinkedIn Legal Job Alert → Telegram

## Setup (10 minuti)
1. **Bot Telegram**: su Telegram scrivi a `@BotFather` → `/newbot` → copia il **token**.
2. **Chat ID**: manda un messaggio qualsiasi al tuo bot, poi apri
   `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia `"chat":{"id": ...}`.
3. **Repo GitHub privato**: carica questi file (inclusa la cartella `.github`).
4. **Secrets**: Settings → Secrets and variables → Actions → aggiungi
   `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
5. **Test**: tab Actions → "LinkedIn job alert" → *Run workflow*.

## Personalizzare
Tutto è in cima a `bot.py`: keyword, raggio, filtri su titolo e azienda.
- Il filtro `COMPANY_EXCLUDE` scarta gli studi legali (ruoli solo in-house). Toglilo se vuoi anche gli studi.
- Il primo run potrebbe mandare diverse notifiche (offerte delle ultime 2h); poi solo le nuove.

## Limiti
- Il cron di GitHub può ritardare di 5-15 minuti nelle ore di punta.
- Endpoint non ufficiale: se LinkedIn cambia l'HTML, i selettori in `fetch()` vanno aggiornati.
