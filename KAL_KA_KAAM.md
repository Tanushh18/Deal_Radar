# DealRadar — kal subah ka kaam

## Ho chuka hai (14 Oct)
- [x] Code `main` pe push (`fffde18`): standby server + app ka dead-server fix
- [x] OTA publish (preview + production)
- [x] Main server `dealradar-0oza` (ingest): env set, naya code live
      (`USER_NODE_URL` = dn91, `PUBLIC_URL` = dealradar.tanush.qd.je)

## Karna hai

### 1. ⚠️ Sabse pehle: teeno naye servers ka env (abhi teeno `ingest` pe chal rahe hain)
Dashboard → service → Environment.

**deal-radar-0q0d (validator)**
```
NODE_ROLE=validator
SELF_URL=https://deal-radar-0q0d.onrender.com
USER_NODE_URL=https://deal-radar-dn91.onrender.com
PUBLIC_URL=https://dealradar.tanush.qd.je
```
`TELEGRAM_SESSION` hata do (padi rahe to bhi chalega, par hatana safe hai)

**deal-radar-dn91 (user)**
```
NODE_ROLE=user
SELF_URL=https://deal-radar-dn91.onrender.com
PUBLIC_URL=https://dealradar.tanush.qd.je
```
`TELEGRAM_SESSION` aur `USER_NODE_URL` hata do (optional)

**deal-radar-jl4z (standby)**
```
NODE_ROLE=standby
SELF_URL=https://deal-radar-jl4z.onrender.com
PUBLIC_URL=https://dealradar.tanush.qd.je
TELEGRAM_SESSION=<0oza wala same>
```
`USER_NODE_URL` hata do (optional)

### 2. Chaaron servers pe ye keys same honi chahiye (0oza se copy karo)
`SECRET_KEY`, `ADMIN_TOKEN`, `MONGODB_URI`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `TURSO_DB_02`, `TURSO_DB_02_AUTH_TOKEN`,
`FCM_SERVICE_ACCOUNT_JSON`, `TG_BOT_TOKEN`, `TG_POST_CHANNEL`

### 3. Settings check (har naye server pe)
- Runtime: Python 3
- Build: `pip install --upgrade pip && pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Branch: `main`, health check `/api/ping`

### 4. Teeno pe Manual Deploy → "Deploy latest commit"

### 5. Claude ko bolo: "check karo" → chaaron ka `/api/ping` dekh ke role confirm karega
Expected: 0oza=ingest, 0q0d=validator, dn91=user, jl4z=standby (`"standby": true`)

### 6. Optional
- Stashr registry (`password-manager-server-xxdr` → `/registry/dealradar`) mein
  `dealradar.ggnhome.com` hata ke chaaron naye URL daalo
- `test_sale_events` wala purana failing test fix karna (bina date wale sale events)
- Suspended `dealradar-93do` aur `dealradar-t04t` ko Render se delete karna hai ya nahi, decide karo
