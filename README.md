# Starlink Mini · Dashboard

A browser dashboard for Starlink Mini dishes. Connects to the dish and
Starlink router over the local gRPC API and serves a live dashboard
at `http://127.0.0.1:8800`.

## Quick start

```bash
chmod +x starlink-web.py
./starlink-web.py
```

Then open <http://127.0.0.1:8800> in a browser.

The script automatically:

1. Creates a Python virtual environment in `./venv` if one doesn't
   already exist
2. Re-executes inside the venv
3. Installs any missing dependencies (`grpcio`, `grpcio-reflection`,
   `protobuf`, `segno`, `cryptography`) — existing installs are left
   alone

## CLI flags

```
./starlink-web.py --host 0.0.0.0 --port 8800
./starlink-web.py -b                    # detach and keep running
./starlink-web.py --stop                # stop a detached instance
```

- `--host` — bind address (default `127.0.0.1`)
- `--port` — TCP port (default `8800`)
- `-b` / `--background` — detach from the terminal and keep serving.
  Prints the child PID, then exits. Child stdout/stderr are appended
  to `--log`.
- `--log` — log file for background mode (default `./starlink-web.log`)
- `--pidfile` — PID file path (default `./starlink-web.pid`)
- `--stop` — send `SIGTERM` to the PID recorded in `--pidfile`, wait
  up to 5 s, then `SIGKILL` if necessary, and remove the pidfile

Use `--host 0.0.0.0` to expose the dashboard to other machines on your
LAN. There is no auth on the HTTP endpoint — only do this on trusted
networks.

Background mode is POSIX-only (Linux / macOS). Starting `-b` while an
instance is already recorded in the pidfile is refused; use `--stop`
first.

## Tabs

### Telemetry

Live dish state, refreshed on a timer (toggle with **Live refresh**):

- **Throughput** — download / upload with animated bars, latency, drop
  rate, SNR, uptime
- **Dish Status** — alerts, pointing, obstruction fraction, heating,
  ready states
- **GPS & Location** — coordinates, altitude, speed, accuracy, and
  one-click links to Google Maps or OpenStreetMap
- **Alignment** — sky view SVG showing current boresight vs. desired
  target plus the alignment filter state (CONVERGED, INITIALIZING…)
- **Signal & Ready States** — SCP / L1L2 / XPHY / AAP / RF
- **Obstruction Map** — SVG heatmap with clear / partial / blocked
  legend, min elevation, max theta
- **Device Info** — hardware / software version, serial, country,
  boot count
- **Connected Routers** — routers the dish reports as paired

### History

- **Power Draw** — current watts plus sparkline, min / max / avg, sample
  window
- **Outages** — recent outages with duration and cause
- **Event Log** — timestamped events reported by the dish

### Router

Everything the Starlink router exposes on `192.168.1.1:9000`:

- **Router Status** — uptime, connectivity, firmware
- **WiFi Clients** — connected device list with signal, rate, vendor
- **WiFi Networks** — SSID list with actions:
  - **Show password** — reveal a password you previously saved in the
    vault (or that the router is willing to return)
  - **Show QR** — render a Wi-Fi QR code (`WIFI:T:WPA;S:...;P:...;;`)
    that phones can scan to join
  - **Save password** — store a masked-but-known password into the
    encrypted vault so it can be re-shown / re-QR'd later
  - **Forget** — drop the saved password from the vault
- **Radio Stats** — per-radio channel, noise floor, tx power
- **Self-test** — live self-test results
- **Network Interfaces** — every iface with IP, MAC, MTU, rx/tx counters

### Configuration

Read-only mirror of the dish configuration. The LAN gRPC endpoint
rejects writes with `PERMISSION_DENIED` — change settings from the
Starlink mobile app.

- **Snow Melt** mode (ON / AUTO / OFF)
- **Power Save Schedule** with a 24-hour timeline showing the active
  sleep window and "now" marker
- **Dish Level** mode
- **Software Updates** reboot hour
- **Location Reporting** enabled / disabled
- **Asset Class** (service tier identifier)
- **Raw Configuration** — full JSON in an expandable block

### Actions

- **Reboot Dish** — confirmation-gated, connectivity drops ~90 s
- **Ping Test** — ICMP echo to `192.168.100.1`, 5 packets
- **Export Full Dump** — downloads every telemetry/config endpoint as
  a single JSON file

### Advanced

- **Raw gRPC Request** — send any whitelisted request key with a JSON
  payload and inspect the raw response
- **gRPC Services** — list every discovered service via reflection
- **Request Fields** — list every request type with its fields
- **Diagnostics** — full `getDiagnostics` dump

Only read-only keys are whitelisted — writes are blocked server-side.

## Wi-Fi password vault

Saved Wi-Fi passwords are stored in `./.starlink-vault.json`, encrypted
with AES-GCM using a key derived from a master password (scrypt).

- The first time you open the Wi-Fi tools you'll be asked to set a
  master password. This initialises the vault.
- Subsequent sessions prompt for the master password to unlock the
  vault; locked state means the dashboard can't read or write
  passwords, and QR codes are refused with `401 locked`.
- The 🔒 button in the header re-locks the vault without restarting
  the server.
- **Forgot it? Reset vault** wipes `.starlink-vault.json` entirely
  (every saved password is lost).

The file has mode `0600`. Anyone with read access to the file still
needs the master password to decrypt entries.

## Languages

The UI ships with English (🇬🇧) and Norwegian Bokmål (🇳🇴). Use the
language dropdown in the header to switch; the choice persists in
`localStorage`.

## Requirements

- **Python 3.9+**
- **Linux** (tested on Manjaro)
- **Starlink Mini dish** reachable at `192.168.100.1:9200`
- **Starlink router** reachable at `192.168.1.1:9000` (optional — the
  Router tab will be empty without it)
- A modern browser (Chrome, Firefox, Safari) — the UI uses `<dialog>`,
  `fetch`, and modern CSS

Dependencies are auto-installed into `./venv`:

- `grpcio`, `grpcio-reflection`, `protobuf` — gRPC client + reflection
- `segno` — Wi-Fi QR code generation
- `cryptography` — AES-GCM + scrypt for the vault

## Connection addresses

```
Dish    192.168.100.1:9200   (gRPC, unauthenticated)
Router  192.168.1.1:9000     (gRPC, unauthenticated)
Web UI  127.0.0.1:8800       (HTTP, unauthenticated)
```

Discovery on both dish and router is automatic via gRPC reflection.
To change the dish address, edit `DISH_ADDR` in `starlink-mini.py` (it
is re-imported by `starlink-web.py`). The router address lives at the
top of `starlink-web.py` as `ROUTER_ADDR`.

## Troubleshooting

**Dish shows "not connected"**
- Check `ping 192.168.100.1`
- Click the ↻ reconnect button in the header
- The `Dish` connection dot in the header shows live state

**Router tab is empty**
- Router isn't reachable at `192.168.1.1:9000`
- Click the ↻ reconnect button next to **Router** in the header

**"PERMISSION_DENIED" on a config change**
- Expected — the LAN API is read-only for writes. Use the Starlink
  mobile app instead.

**Wi-Fi QR won't scan**
- Make sure the vault is unlocked and the SSID actually has a password
  saved (Show QR needs a real password, not the masked placeholder)
- Verify the modal shows the SSID you expect

**Vault says "locked"**
- Click 🔓 in the header (or reopen a Wi-Fi action) and enter the
  master password

**Port already in use**
- Pass `--port 8801` (or any free port)

## Files

```
starlink-web.py           HTTP server + gRPC proxy
starlink-mini.py          gRPC client library (imported by the web server)
static/index.html         Dashboard markup
static/app.js             Dashboard logic
static/style.css          Dashboard styles
static/i18n.js            en / nb translations
.starlink-vault.json      Encrypted Wi-Fi password vault (gitignored)
```

## Notes

- **No internet required** — everything is local LAN
- **No authentication** on the HTTP endpoint — bind to `127.0.0.1`
  unless you trust your network
- **Read-mostly** — write paths (reboot, raw gRPC) exist but the dish
  rejects most config writes
- **Stateless server** — the only persistent state is the Wi-Fi vault

## Acknowledgments

This project was co-created with
[Claude](https://www.anthropic.com/claude) (Anthropic's Claude Code
CLI). Pair-programming contributions span the gRPC proxy, the web
dashboard, the Wi-Fi vault, and the venv bootstrap.

---

**Run:** `./starlink-web.py`
**Open:** <http://127.0.0.1:8800>
**Stop:** `Ctrl+C` in the terminal
