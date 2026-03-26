---
status: published
repo: https://github.com/spinnakergit/a0-telegram
index_pr: https://github.com/agent0ai/a0-plugins/pull/61
published_date: 2026-03-11
version: 1.1.0
---

# Release Status

## Publication
- **GitHub**: https://github.com/spinnakergit/a0-telegram
- **Plugin Index PR**: [#61](https://github.com/agent0ai/a0-plugins/pull/61) (CI passed)
- **Published**: 2026-03-11

## Verification Completed
- **Automated Tests**: 58/58 PASS (regression suite, 2026-03-11)
- **Human Verification**: 76/76 PASS, 0 failures, 0 skipped (2026-03-11)
  - Phase 1-2: WebUI + Config & Connection: 16/16
  - Phase 3-7: Tools (read, send, members, summarize, manage): 19/19
  - Phase 8-12: Bridge (lifecycle, restricted, auth, elevated, session): 23/23
  - Phase 13-14: Security (injection + access control): 9/9
  - Phase 15-16: Edge cases + documentation: 9/9
- **Security Assessment**: PASS (2026-03-11)
  - 1 Critical fixed (VULN-001: auth keys in message_store.json)
  - 1 High fixed (VULN-002: world-readable message_store.json)
  - 2 Medium accepted (framework-level CSRF dependencies)
  - 2 Low accepted (minimal risk)
  - 3 Informational (framework-level issues)

## Issues Found & Fixed During Verification
| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `!status` command not recognized | Low | Fixed |
| 2 | Elevated mode infection check false positive | High | Fixed |
| 3 | Config API merge behavior for allowlist | Low | Documented (framework limitation) |
| 4 | Auto-start extension non-functional | High | Fixed (complete rewrite) |
| 5 | README security section insufficient | Medium | Fixed |
| 6 | getUpdates conflict with simultaneous polling | High | Fixed (guard function) |

## Changelog

### v1.1.0 — 2026-03-25
Standards conformance release.
- **config.html**: Migrated to A0's standard Alpine.js `x-model` settings framework (removes custom fetchApi/save logic)
- **telegram_config_api.py**: Simplified to only handle `generate_auth_key` (config CRUD now handled by framework)
- **main.html**: Fixed CSRF timing bug (lazy fetchApi) and stale addEventListener on component reload (inline onclick + window namespace)
- **install.sh**: Skip file copy when installed in-place via Plugin Hub
- **hooks.py**: New install/uninstall lifecycle hooks with proper logging
- **docs/SETUP.md**: New — full credential setup guide (BotFather, 3 install options, credential mapping, rate limits, troubleshooting)
- **docs/QUICKSTART.md**: Added Known Behaviors, Troubleshooting table, Plugin Hub install option, credential mapping
- **docs/DEVELOPMENT.md**: Added Alpine.js config pattern, main.html dashboard pattern, architecture decisions, testing section
- **docs/README.md**: Added SETUP link, verification badge, updated API endpoint descriptions
- **thumbnail.png**: Added (256x256 RGBA, transparent corners)
- **.gitignore**: Added `.toggle-*`, `tests/*.json`
- **README.md**: Added verification badge section
- All `print()` in hooks.py replaced with `logging.getLogger()`

### v1.0.0 — 2026-03-11
Initial release.
- 6 tools: read, send, members, summarize, manage, chat
- 3 API endpoints: test, config, bridge
- Chat bridge with dual-mode security (restricted/elevated)
- 3 skills: research, communicate, chat
- Auto-start extension for chat bridge
- Full verification: 58/58 regression, 76/76 HV, security assessed
