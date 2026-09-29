# Deal_Radar: Issues & Analysis Report

## Executive Summary

Deal_Radar is a mature Telegram deal aggregator with a Python FastAPI backend and React Native Expo mobile app. The project has **no open GitHub issues** and shows strong architectural patterns with defensive exception handling. Analysis identified several areas for improvement across backend, mobile, and infrastructure.

---

## Backend Issues (Python/FastAPI)

### 1. **Turso Sync Interval Race Condition** (Medium Priority)
**File:** `app/db.py`
**Issue:** The `_last_sync` global tracks sync time, but the sync operation itself can take significant time. On slow networks or high load, overlapping sync attempts could occur.
```python
# Current: checks if time elapsed, but doesn't prevent concurrent syncs
if time.time() - _last_sync >= settings.turso_sync_seconds:
    # sync happens here, but another thread could start before this completes
    _last_sync = time.time()
```
**Fix:** Use a lock to prevent concurrent syncs, not just time-based spacing.

### 2. **Missing Validation on Price Values** (Low Priority)
**File:** `app/routers/deals.py` (price_verdict function, line 169)
**Issue:** Division by zero protection exists, but no guard against NaN or Infinity floats from Turso.
```python
def price_verdict(price, stats):
    price, low, median = float(price), float(stats["min"]), float(stats["median"])
    # If Turso returns inf or nan, comparisons fail silently
```

### 3. **Image Cache Unbounded Memory Risk** (Low Priority)
**File:** `app/routers/deals.py` (line 19-20)
**Issue:** The image LRU cache hardcodes 120 max images. On 512MB Render dyno, each image can be 100-500KB; if images average 200KB, that's 24MB just for images. No pressure valve if Telegram returns larger images.
```python
_image_cache: "OrderedDict[str, bytes]" = OrderedDict()
_IMAGE_CACHE_MAX = 120
```

### 4. **No Timeout on Telegram Link Liveness Probes** (Medium Priority)
**File:** `app/services/ingest.py` (line 29-30, httpx client)
**Issue:** Liveness checks fetch deal links but the timeout configuration is not visible. If a link hangs, the entire ingest cycle stalls.
```python
import httpx
# No timeout context visible in httpx calls
```

### 5. **Mongo Restore Partial Failure Silent Fallback** (Low Priority)
**File:** `app/main.py` (lifespan, line 78-90)
**Issue:** If `restore_user_channels()` fails partway through, no rollback occurs. A user might see their channels partially restored.
```python
try:
    meta = await loop.run_in_executor(None, mongo_store.restore_all_meta)
except Exception as exc:  # only logs, doesn't invalidate partial results
    log.warning("Meta restore failed: %s", exc)
```

### 6. **Session Encryption Key Derivation** (Security: Low Risk)
**File:** `app/config.py` (line 166-167)
**Issue:** Fernet key derived from SHA256 hash. While acceptable, using HMAC or KDF (like scrypt/argon2) would be more robust, especially if SECRET_KEY is weak.
```python
@property
def fernet_key(self) -> bytes:
    digest = hashlib.sha256(self.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
```

### 7. **No Connection Pool Management for Turso** (Low Priority)
**File:** `app/db.py` (line 22-25)
**Issue:** Turso connection opened once globally. No connection health check or retry logic if the connection becomes stale over hours of operation.

### 8. **Missing Rate Limit on Bulk Operations** (Medium Priority)
**File:** `app/routers/deals.py` (/sparklines endpoint, line 138-166)
**Issue:** While rate-limited to 60/min, a single request can process up to 100 deal IDs. Malicious actor could request 100 different product histories simultaneously, spiking DB load.

---

## Mobile App Issues (React Native/Expo)

### 1. **No Error Boundary Component** (Medium Priority)
**File:** `mobile/src/components/AppProviders.tsx`, `mobile/App.tsx`
**Issue:** React crash (red screen) in Expo will crash the entire app. No error boundary to catch runtime errors gracefully.

### 2. **AsyncStorage Fallback Missing** (Low Priority)
**Issue:** AsyncStorage operations can fail on some Android devices. No fallback to in-memory cache or graceful degradation.
```typescript
// No try-catch around AsyncStorage reads/writes in many places
```

### 3. **Missing Network Timeout Configuration** (Medium Priority)
**File:** `mobile/src/api/index.ts`
**Issue:** Fetch calls to the server have no explicit timeout. On slow connections, requests hang indefinitely.

### 4. **Notification Permission Request Race Condition** (Low Priority)
**File:** `mobile/src/native/notifications.ts`
**Issue:** Permission is requested ~6 seconds after launch, but if the user opens a notification before then, it may fail.

### 5. **No Deep Link Validation** (Low Priority)
**File:** `mobile/src/native/deepLinks.ts`
**Issue:** Deep links are parsed but not validated. A malformed deep link parameter could crash the app or navigate to unexpected screens.

### 6. **Background Task No Retry Logic** (Low Priority)
**File:** `mobile/src/native/backgroundTask.ts`
**Issue:** Background feed poll task doesn't retry on network failure. If the server is momentarily unavailable, devices miss that cycle's updates.

---

## Infrastructure & Deployment Issues

### 1. **Render Free Tier 512MB RAM Constraint** (Medium Priority)
**Issue:** With Turso history cache enabled, if a deal has months of price history, memory usage grows. No memory limit enforcement.
- Mitigated by: `LOCAL_CACHE_DAYS` (default 15), but could be tightened

### 2. **No Graceful Degradation on Turso Outage** (Medium Priority)
**Issue:** If Turso is unavailable, the app falls back to ephemeral SQLite. Price history and full deals vanish on restart.
- Partial mitigation: `turso_backup.start()` retries, but needs exponential backoff

### 3. **Missing Setup Validation Script** (Low Priority)
**Issue:** No bootstrap script to validate all env vars are set before starting. A missing `TURSO_AUTH_TOKEN` causes cryptic errors mid-operation.

### 4. **No Automated Backups of SQLite** (Low Priority)
**Issue:** If Render instance dies before Turso sync, in-flight updates are lost.

---

## Code Quality Issues

### 1. **Telethon Client Lifecycle** (Low Priority)
**File:** `app/services/telegram.py`
**Issue:** Telethon clients are cached per user but have no TTL. A user who stops using the app still holds a live Telegram session indefinitely.

### 2. **Missing Type Hints in Several Services** (Low Priority)
**Files:** Some services lack full type hints, making IDE autocomplete unreliable.

### 3. **Inconsistent Error Response Format** (Low Priority)
**Issue:** Some endpoints return `{"status": "...", "detail": "..."}`, others return `{"error": "..."}`. Client needs to handle multiple formats.

### 4. **No Observability/Logging for Long Operations** (Low Priority)
**Issue:** The ingest cycle does 7 steps but only logs start/end. If step 5 (verify links) hangs, there's no visibility into which step is slow.

---

## Configuration & Documentation Issues

### 1. **Default `POLL_INTERVAL_SECONDS` Too Long** (Low Priority)
**Issue:** Default is 2400s (40 min). For a fast-moving deal channel, deals posted in the first minute disappear from cache before the next poll. README recommends 5 min for local dev but defaults to 40 min.

### 2. **`PRIORITY_AUDIENCE` Default is "women"** (Low Priority)
**Issue:** Hardcoded assumption in `app/config.py` line 36. If a deployment serves a different market, deals don't rank correctly without explicit override.

### 3. **Missing Environment Variable Documentation** (Low Priority)
**Issue:** `.env.example` is not comprehensive. Several advanced vars like `GROQ_MAX_CALLS_PER_CYCLE`, `BUYHATKE_WARM_PER_CYCLE` are not mentioned.

---

## Testing & CI/CD Issues

### 1. **Test Dependencies Not in `requirements.txt`** (Medium Priority)
**File:** `requirements.txt`
**Issue:** `pytest` is not listed. Running `python -m tests.test_pipeline` fails with `ModuleNotFoundError: No module named 'pytest'` unless pytest is manually installed.
- Workaround: `python -m tests.test_notify_auto` works (doesn't need pytest)

### 2. **No CI/CD Pipeline** (Medium Priority)
**File:** `.github/workflows/`
**Issue:** EAS workflows exist for mobile builds, but no Python linting, type checking, or API tests in CI.

### 3. **Missing Android ProGuard Configuration** (Low Priority)
**File:** `mobile/android/app/build.gradle` (generated)
**Issue:** Release APK may have minified method names in stack traces, making debugging harder.

---

## Security Observations

### 1. **SSRF Guarding on Link Probes** ✅ (Good)
**File:** `app/services/ingest.py` (line 529-533 in README)
- Properly blocks private/loopback IPs before fetching links.

### 2. **Session Encryption** ✅ (Good)
- Telethon sessions are Fernet-encrypted before storage.

### 3. **CORS Handling** ✅ (Good)
- Correctly disables credentialed CORS when wildcard origins are set.

### 4. **Rate Limiting** ⚠️ (Partial)
- `/send-code` protected (prevents abuse), but bulk operations like `/sparklines` could use tighter limits.

---

## Recommendations (Priority Order)

| Priority | Issue | Effort | Impact |
|---|---|---|---|
| **High** | Test dependencies missing from requirements.txt | 5 min | Blocks testing in CI |
| **High** | No timeout on Telegram link liveness probes | 30 min | Ingest cycle can hang indefinitely |
| **Medium** | Image cache unbounded memory risk on 512MB dyno | 15 min | Potential OOM on long-running instances |
| **Medium** | Rate limit on bulk `/sparklines` requests | 20 min | DB DoS vector |
| **Medium** | Mobile app missing error boundary | 1 hour | App crash on React errors |
| **Low** | Turso sync race condition with global `_last_sync` | 30 min | Edge case data corruption |
| **Low** | No validation on price floats from Turso (NaN/Inf) | 15 min | Silent UI bugs if data is corrupted |
| **Low** | Mongo partial restore silent failure | 1 hour | User experience degradation |

---

## Notes

- **No open GitHub issues** suggests mature, stable codebase.
- **Strong exception handling patterns** indicate defensive coding mindset.
- **Architecture is sound**: clear separation between Turso (permanent), SQLite (cache), MongoDB (metadata).
- **Main risk is at scale**: 40+ channels, high-frequency polls, or Render free-tier resource contention could trigger edge cases.

