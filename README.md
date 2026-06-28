# Forex Signal Bot 🚨
### HTF OB + LTF MSS — Free Server par 24/7

**Kya karta hai:**
- Twelve Data se live Forex + Gold data fetch karta hai
- HTF Order Blocks dhundta hai
- LTF MSS + FVG confirm karta hai  
- Telegram par alert + chart bhejta hai
- **Railway.app par FREE mein 24/7 chalta hai**

---

## Step 1 — Config fill karo

`config.yaml` kholo aur yeh 3 cheezein fill karo:

```yaml
twelve_data:
  api_key: "yahan apni key"      # twelvedata.com → Dashboard

telegram:
  bot_token: "yahan token"       # @BotFather se
  chat_id: "yahan chat_id"       # @userinfobot se
```

---

## Step 2 — GitHub par daalo

1. **github.com** par account banao (free)
2. New repository banao → naam: `forex-signal-bot`
3. Yeh saari files us repo mein upload karo

---

## Step 3 — Railway par deploy karo (FREE)

1. **railway.app** jaao → "Start a New Project"
2. "Deploy from GitHub repo" select karo
3. Apna `forex-signal-bot` repo select karo
4. **Variables** tab mein jaao → inhe add karo:

```
TWELVE_DATA_API_KEY = tumhari_api_key
TELEGRAM_BOT_TOKEN  = tumhara_token
TELEGRAM_CHAT_ID    = tumhara_chat_id
```

5. Bot automatically deploy ho jayega
6. Logs mein dekho — "Bot live!" dikhna chahiye
7. Telegram par startup message aayega

**Railway free tier: $5/month credit — ek chota bot ke liye kaafi hai**

---

## Telegram Alert Example

```
🚨 MSS SIGNAL ▲
━━━━━━━━━━━━━━━━━━━━
📊 Pair:      XAU/USD
📈 Direction: LONG  🟢
🕯 Cascade:   H4 OB → M15 MSS
━━━━━━━━━━━━━━━━━━━━
💰 Entry:     1923.45000
🛑 SL:        1918.20000
🎯 TP1 (1:2): 1933.95000
🎯 TP2 (1:3): 1939.20000
━━━━━━━━━━━━━━━━━━━━
📐 Risk:    525 pips
💵 Reward:  1575 pips
⚠️ Signal only — verify before trading
[Chart Screenshot]
```

---

## API Call Budget (Free Tier)

Twelve Data free = **800 calls/day**

| Scan | Calls Used |
|------|-----------|
| 7 pairs × 4 cascades × 2 TFs | ~56 calls |
| Price check × 7 pairs | ~7 calls |
| **Total per scan** | **~63 calls** |
| Scan interval: 15 min | **~63 × 32 = ~2016/day** |

⚠️ Free tier ke liye **scan_interval_seconds: 1800** (30 min) rakho ya pairs kam karo.

---

## Common Errors

| Error | Fix |
|-------|-----|
| `Telegram connect nahi hua` | Bot token check karo; apne bot ko ek message bhejo |
| `Twelve Data connect nahi hua` | API key check karo twelvedata.com par |
| `No HTF data` | Symbol name check karo — Twelve Data mein `XAU/USD` hai, `XAUUSD` nahi |
| Railway crash loop | Logs dekho → config.yaml variables sahi hain? |
