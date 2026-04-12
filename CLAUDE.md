# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
./starlink-web.py                 # serve dashboard on http://127.0.0.1:8800
./starlink-web.py --host 0.0.0.0 --port 8800
./starlink-web.py -b              # detach (writes pidfile + log)
./starlink-web.py --stop          # SIGTERM the pidfile'd instance
./starlink-mini.py                # TUI variant (same gRPC client, no HTTP)
```

Both scripts bootstrap themselves: on first run they create `./venv/`, re-exec inside it, and install only *missing* deps (`grpcio`, `grpcio-reflection`, `protobuf`, `segno`, `cryptography`). There is no separate build/test/lint step — no test suite exists.

Pidfile/log defaults for `-b`: `./starlink-web.pid`, `./starlink-web.log`. Background mode is POSIX-only and refuses to start if the pidfile points at a live pid.

## Architecture

Two entry points share one gRPC client. `starlink-web.py` serves a browser dashboard; `starlink-mini.py` is a standalone fullscreen TUI. They are **siblings, not layered** — `starlink-web.py` imports `starlink-mini.py` via `importlib` purely to reuse `DishClient` and `DISH_ADDR`, not for display code.

**gRPC discovery.** `DishClient` (in `starlink-mini.py`) has no compiled `.proto` files. It connects to the dish/router, lists services via gRPC reflection, walks dependencies to build a `DescriptorPool`, then calls `Handle` on whatever service exposes it. Every request is `json_format.Parse({key: body}, Request)` → unary call → `MessageToDict`. This is why the same client can talk to both `192.168.100.1:9200` (dish) and `192.168.1.1:9000` (router) — it learns their schemas at connect time.

**Permission whitelist.** `DishClient.PERMITTED_KEYS` is the security boundary: any key not in the set is refused before hitting gRPC. `starlink-web.py` *extends* this set at import time (see the `DishClient.PERMITTED_KEYS = frozenset(... | {...})` block near the top) rather than editing `starlink-mini.py`, so the web layer owns its own contract. The only write key is `reboot`; everything else is read-only. The dish itself returns `PERMISSION_DENIED` for config writes — change settings from the Starlink mobile app.

**Two proxies, one client class.** `starlink-web.py` instantiates `DishProxy(DISH_ADDR)` and `DishProxy(ROUTER_ADDR)` — same class, different endpoints. Each proxy owns a lock used *only* to snapshot/swap the underlying `DishClient` (so reconnects don't race); the blocking gRPC call itself runs outside the lock so concurrent requests don't serialize. A module-level `_GRPC_SEMAPHORE = BoundedSemaphore(8)` caps total in-flight gRPC calls because `getHistory` / `dishGetObstructionMap` can return up to 50 MB.

**HTTP layer.** `ThreadingHTTPServer` with hand-rolled routes. `DISH_KEY_MAP` and `ROUTER_KEY_MAP` at ~line 544 are the tables that translate `/api/*` paths to gRPC keys; most endpoints are a one-liner through `_proxy_fetch`. Vault, Wi-Fi QR, and `/api/raw` are the non-trivial handlers. Every response gets `_SECURITY_HEADERS` (strict CSP, no inline, `frame-ancestors 'none'`). POSTs also run `_check_host` + `_check_origin` to block DNS rebinding and cross-origin writes.

**Wi-Fi vault.** `.starlink-vault.json` is AES-GCM-encrypted with a PBKDF2-SHA256 (600k iter) key derived from a master password. The key only lives in `_vault_key` after `/api/vault/unlock`; locking zeroes it. `/api/vault/unlock` has a per-IP consecutive-failure counter (bounded at 256 entries). The vault exists because current dish firmware masks Wi-Fi PSKs as `•••••` over the LAN API, so the dashboard lets the user paste the real one once and re-serve it for Show Password / Show QR.

**Frontend.** `static/{index.html,app.js,style.css,i18n.js}` — no build step, no framework. `i18n.js` holds en/nb translation tables keyed by `data-i18n` attributes, language choice persists in `localStorage`. `app.js` polls `/api/*` endpoints on a timer controlled by the Live Refresh checkbox.

## Things to know before editing

- **Don't add compiled protobufs.** The reflection-based discovery is intentional — it makes the client firmware-agnostic. If you need a new endpoint, add the key to `PERMITTED_KEYS` (in `starlink-web.py`'s extension block, not the library) and a row to `DISH_KEY_MAP` / `ROUTER_KEY_MAP`.
- **snake_case vs camelCase.** The dish returns both depending on the key. Display code in `starlink-mini.py` uses `sg_coalesce(d, ["device_info", "deviceInfo"])` to paper over this — follow the pattern.
- **NaN strings.** The API sometimes returns the string `"NaN"` instead of null; display code must treat it as N/A (see `pf()` in `starlink-mini.py`).
- **Read-only by contract.** If you're tempted to add a write endpoint, check: the dish will reject it with `PERMISSION_DENIED` anyway. The only surviving write is `/api/reboot`.
- **No auth on HTTP.** Default bind is `127.0.0.1`. `--host 0.0.0.0` is a documented escape hatch for LAN use — don't assume the server is unreachable from other machines.
- **`starlink-web.py` imports `starlink-mini.py` by file path**, not as a module. Renaming `starlink-mini.py` breaks the web server; `MINI_PATH` at the top of `starlink-web.py` is the pointer.
