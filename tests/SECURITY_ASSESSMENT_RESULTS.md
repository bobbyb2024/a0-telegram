# Security Assessment: Telegram Integration

**Date:** 2026-03-11
**Assessor:** Claude Code (Opus 4.6) — manual penetration test
**Target:** agent-zero-dev-latest:50084
**Plugin Version:** 1.0.0
**Methodology:** SECURITY_ASSESSMENT_FRAMEWORK.md (5-phase assessment)

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High | 1 |
| Medium | 2 |
| Low | 2 |
| Informational | 3 |

---

## Findings

### VULN-001: Auth keys stored in plaintext in message_store.json

- **Severity:** Critical
- **CVSS:** 9.1
- **Description:** The `!auth <key>` messages sent by users are persisted in `message_store.json` in plaintext. The message store captures all incoming messages (including auth commands) BEFORE the bridge's `_handle_auth_command()` processes and deletes them from Telegram. This creates a permanent record of every auth key ever used.
- **Reproduction:**
  1. Send `!auth <key>` via Telegram to authenticate
  2. Read `/a0/usr/plugins/telegram/data/message_store.json`
  3. Search for `!auth` — full keys are visible in plaintext
- **Evidence:** Found both old and current auth keys in plaintext (redacted for publication)
- **Impact:** Any process or user with read access to the data directory can extract auth keys and gain elevated access to the chat bridge. Combined with the world-readable file permissions (VULN-002), this is a critical credential exposure.
- **Recommendation:** Filter `!auth` messages from the message store. In `_on_message()`, do NOT call `store_message()` for messages starting with `!`. Alternatively, redact the key before storing (e.g., store `!auth ****` instead).
- **Status:** Fixed — `_on_message()` now skips `store_message()` for all `!` command messages; existing auth entries purged

### VULN-002: message_store.json has world-readable permissions (644)

- **Severity:** High
- **CVSS:** 7.5
- **Description:** The message store file has 644 permissions (owner read/write, group read, world read). This allows any user or process on the system to read all stored messages, including the auth keys from VULN-001.
- **Reproduction:**
  1. `stat -c '%a' /a0/usr/plugins/telegram/data/message_store.json` → `644`
  2. Any unprivileged process can read the file
- **Impact:** Amplifies VULN-001 by making the credential exposure accessible to any process on the container, not just root.
- **Recommendation:** Set message_store.json permissions to 600 (owner read/write only) using `secure_write_json()` or explicit `os.chmod()` after writes.
- **Status:** Fixed — `_save_store()` now calls `os.chmod(path, 0o600)` after every write

### VULN-003: Config write via CSRF allows allowlist manipulation

- **Severity:** Medium
- **CVSS:** 6.5
- **Description:** An attacker who obtains a CSRF token (possible via the known Origin spoofing framework vulnerability) can modify the plugin configuration, including: adding themselves to the `allowed_users` list, disabling `sanitize_content`, changing `session_timeout`, or enabling `allow_elevated`. The A0 config merge behavior means the attacker's user ID is ADDED to the allowlist rather than replacing it, but this still grants them access.
- **Reproduction:**
  1. Get CSRF token: `curl -s -c cookies.txt http://localhost:50084/api/csrf_token -H "Origin: http://localhost:50084"`
  2. Add attacker to allowlist: `curl -b cookies.txt -X POST .../telegram_config_api -H "X-CSRF-Token: <token>" -d '{"action":"save","config":{"chat_bridge":{"allowed_users":["attacker_id"]}}}'`
  3. Verify: `curl ... -d '{"action":"get"}'` — attacker ID now in allowed_users
- **Impact:** Remote attacker can grant themselves chat bridge access, disable security controls, or change auth settings. Requires CSRF token (obtainable via framework Origin bypass).
- **Recommendation:** This is primarily a framework-level issue (Origin bypass on CSRF token endpoint). At the plugin level, consider: (a) requiring re-authentication for security-sensitive config changes, (b) logging config modifications for audit.
- **Status:** Open (framework dependency)

### VULN-004: Bridge API allows remote stop/start (DoS)

- **Severity:** Medium
- **CVSS:** 5.3
- **Description:** An attacker with a CSRF token can remotely stop and restart the chat bridge via the bridge API, causing service disruption for legitimate users.
- **Reproduction:**
  1. Obtain CSRF token (same as VULN-003)
  2. Stop bridge: `curl ... -d '{"action":"stop"}'` → bridge stops
  3. Start bridge: `curl ... -d '{"action":"start"}'` → bridge restarts
- **Impact:** Temporary denial of service for chat bridge users. Sessions are cleared on restart, forcing re-authentication. Limited severity since the bridge can be restarted.
- **Recommendation:** Same framework-level CSRF fix would resolve this. No additional plugin-level mitigation needed beyond what CSRF provides.
- **Status:** Open (framework dependency)

### VULN-005: Auth keys leaked in A0 agent reasoning logs

- **Severity:** Low
- **CVSS:** 3.7
- **Description:** When a user sends `!auth <key>` through the bridge in restricted mode (before the auth command handler processes it), the A0 agent's reasoning logs may include the full auth key in its analysis of the message. These logs are visible via `docker logs`.
- **Reproduction:**
  1. Send `!auth <key>` via Telegram
  2. `docker logs agent-zero-dev-latest 2>&1 | grep "auth"` — key visible in LLM reasoning text
- **Impact:** Low — requires container log access (typically root/admin only). The key is embedded in verbose LLM reasoning output, not prominently displayed.
- **Recommendation:** The `!auth` message handler already deletes the Telegram message. The log exposure is a side effect of A0's reasoning logging. Filtering `!auth` from message storage (VULN-001 fix) would also reduce log exposure since the LLM wouldn't see the auth message in the store.
- **Status:** Open

### VULN-006: Bridge status API exposes bot metadata

- **Severity:** Low
- **CVSS:** 2.1
- **Description:** The bridge status API returns the bot username, bot user ID, and active chat count. This information aids reconnaissance.
- **Reproduction:**
  1. Obtain CSRF token
  2. `curl ... -d '{"action":"status"}'` → returns bot username, ID, chat count
- **Impact:** Minimal — bot username is public on Telegram anyway. Chat count reveals active bridge usage.
- **Recommendation:** Consider omitting `user_id` from status response, as it's not needed by the WebUI.
- **Status:** Open

### INFO-001: CSRF token obtainable via Origin spoofing (Framework)

- **Severity:** Informational (Framework-level)
- **CVSS:** N/A (framework issue)
- **Description:** The A0 CSRF token endpoint (`/api/csrf_token`) accepts any request with an `Origin` header matching the target URL pattern. An attacker can obtain a valid CSRF token by simply including `Origin: http://localhost:50084` in their request. This is the root cause enabling VULN-003 and VULN-004.
- **Impact:** Enables all CSRF-dependent attacks. Already documented in Discord pentest findings.
- **Recommendation:** Report to A0 framework maintainers. Framework should validate CSRF tokens against server-side sessions, not just Origin headers.
- **Status:** Known framework issue

### INFO-002: No plugin isolation (Framework)

- **Severity:** Informational (Framework-level)
- **Description:** Within the container, the Telegram plugin process can read other plugins' configuration files (e.g., Signal's `config.json`). There is no filesystem isolation between plugins.
- **Impact:** A compromised plugin could access other plugins' secrets.
- **Recommendation:** Framework-level concern. Plugin developers should use defense-in-depth (restricted file permissions, masked config values).
- **Status:** Known framework architecture

### INFO-003: infection_check does not cover REST API requests (Framework)

- **Severity:** Informational (Framework-level)
- **Description:** A0's `infection_check` monitors agent loop output (reasoning, responses, tool calls) but does not inspect HTTP requests to plugin APIs. Direct API attacks bypass the infection check entirely.
- **Impact:** Automated attack tools targeting plugin APIs are not detected by the safety system.
- **Recommendation:** Framework-level concern. Plugin APIs must implement their own security (CSRF, input validation, rate limiting) independent of infection_check.
- **Status:** Known framework architecture

---

## What Passed (No Vulnerabilities Found)

| Test | Result |
|------|--------|
| Bot token masking in config API | PASS — masked with only first 2 + last 2 chars visible |
| Auth key masking in config API | PASS — masked with only last 4 chars visible |
| CSRF enforcement (no token) | PASS — all endpoints return 403 |
| HTTP method restriction | PASS — GET returns 403, PUT returns 405 |
| Command injection via config values | PASS — `$(cat /etc/passwd)` stored literally, not executed |
| Path traversal via API params | PASS — extra params ignored |
| Auth timing attack resistance | PASS — hmac.compare_digest is constant-time |
| Auth key entropy | PASS — 258 bits, brute-force infeasible |
| Auth brute-force lockout | PASS — 5 failures / 5 min window (verified in HV testing) |
| Config file permissions | PASS — 600 (owner only) |
| Data directory permissions | PASS — 700 (owner only) |
| State file permissions | PASS — 600 (owner only) |
| Injection sanitization | PASS — verified in HV-59 through HV-63 |
| Session timeout enforcement | PASS — verified in HV-56 |
| User allowlist enforcement | PASS — verified in HV-67 |
| Restricted mode tool denial | PASS — verified in HV-43, HV-44 |

---

## Remediation Tracking

| ID | Severity | Status | Fix Description |
|----|----------|--------|-----------------|
| VULN-001 | Critical | **Fixed** | Skip `store_message()` for `!` commands in `_on_message()`; purged existing entries |
| VULN-002 | High | **Fixed** | `_save_store()` calls `os.chmod(path, 0o600)` after every write |
| VULN-003 | Medium | Accepted | Framework CSRF dependency — document as known limitation |
| VULN-004 | Medium | Accepted | Framework CSRF dependency — document as known limitation |
| VULN-005 | Low | Accepted | Side effect of A0 reasoning logs — will reduce with VULN-001 fix |
| VULN-006 | Low | Accepted | Minimal risk — bot username is public |
| INFO-001 | Info | Known | Framework — report to A0 maintainers |
| INFO-002 | Info | Known | Framework architecture limitation |
| INFO-003 | Info | Known | Framework architecture limitation |

---

## Gate Assessment

Per the Security Assessment Framework:
- **Critical/High must be fixed before publishing**
- VULN-001 (Critical): **Fixed** — verified no auth keys in message store
- VULN-002 (High): **Fixed** — verified permissions = 600
- All Medium/Low/Info findings are documented with mitigation strategies

**Status: PASS** — all Critical/High findings remediated and verified. Ready for publishing.
