# Engineering Coach — Example Mappings

Each example shows: **User phrasing** (non-engineering) → **Engineering framing**
→ **How real teams solve it** → **What to build**.

---

## 1. Login / session token (the case that motivated this skill)

**User says:** "登录后刷新页面不要掉线；关闭浏览器要重新登录；最好关掉标签页也要重新登录。还有别让人截图就把账号拿走。"

**Engineering framing:** Session / credential lifecycle management.
- "刷新不掉线" → credential must survive a full page reload.
- "关浏览器重登" → credential must be non-persistent (session scope).
- "关标签页也重登" → credential must be tab-scoped, not browser-scoped.
- "别被截图拿走" → credential must never appear in the URL / browser history.

**How real teams solve it (industry standard):** This is one of the most
standard problems in web engineering. The mature pattern is **server-side
session + HttpOnly cookie + refresh token**:
- Access token: short-lived, stored in an HttpOnly, Secure, SameSite cookie (never
  readable by JS, never in the URL). Survives refresh, dies when the browser
  closes (session cookie). Sent automatically via the request header.
- Refresh token: longer-lived, used to obtain a new access token; revocable,
  stored server-side so "revoke all devices" works.
- Tab-close invalidation is intentionally NOT enforced by most products — a
  browser-scoped cookie is the norm (closing one tab keeps you logged in
  elsewhere). Per-tab invalidation requires fragile client-side heuristics
  (visibilitychange timeouts) that cannot reliably distinguish "tab closed" from
  "tab switched" or "refresh", so industry generally accepts browser-scope.

**What to build:** A session cookie (no `expires`) holding a server-issued token,
read by the server from the request header (`st.context.cookies` in Streamlit).
Token has a server-side TTL (e.g. 12h) and supports revocation. Accept that
closing a single tab keeps login (standard web behavior); the security goal
(URL/history exposure) is fully met. Do not hard-code a brittle tab-close
detector.

**Pitfall we hit (why this skill has an anti-churn rule):** we first tried to
satisfy all three wishes with a `visibilitychange` tab-close detector, which
produced several fragile rewrites before reverting to the plain session cookie.
The standard answer was right from the start — the churn came from fighting the
platform model, not from the problem being hard. The lesson: detect the conflict
in round one and adopt the industry pattern, instead of iterating on a
contradictory spec.

---

## 2. "Two people editing the same note at once will clash"

**User says:** "两个人同时改同一个笔记会不会乱？"

**Engineering framing:** Concurrent modification / optimistic concurrency control.

**How real teams solve it:** Optimistic concurrency (version number or
last-write-wins with conflict detection), or operational transforms / CRDTs for
true collaborative editing (Google Docs model). Simpler apps use a version
stamp: reject the second save if the base version changed, prompting the user to
merge.

**What to build:** Store a `version` per record; on save, require the client's
version to match; on mismatch return a conflict the UI surfaces. Avoid silent
last-write-wins that loses data.

---

## 3. "What if the server crashes / we lose data?"

**User says:** "服务器崩了数据会不会没？"

**Engineering framing:** Durability, backup, and fault tolerance.

**How real teams solve it:** Durable storage with write-ahead / transactional
commits (a real database, not flat files), periodic backups, and replication.
For local apps, at minimum use a proper embedded DB (SQLite) with transactions
and scheduled backups.

**What to build:** Persist to a transactional store; add backup/export. Never
rely on in-memory or append-only text files for source-of-truth data.

---

## 4. "Will it get slow with many users / notes?"

**User says:** "笔记多了会不会卡？"

**Engineering framing:** Query performance, indexing, pagination.

**How real teams solve it:** Index the columns used in filters/search, paginate
results, avoid loading everything into memory, cache hot reads.

**What to build:** Add DB indexes on `user_id`, `created_at`; paginate list
views; debounce search.

---

## 5. "Someone could spam the button / hammer the API"

**User says:** "别人一直点会不会炸？"

**Engineering framing:** Rate limiting / abuse protection.

**How real teams solve it:** Per-user/IP rate limits (token bucket), request
validation, and CAPTCHA on sensitive endpoints.

**What to build:** Throttle repeated identical requests; validate input server-side.
