# Starlink Mini · Control Center

A fullscreen TUI control center for Starlink Mini dishes. Access real-time telemetry, diagnostics, configuration, and control via the gRPC API.

## Installation

```bash
chmod +x starlink-mini.py
./starlink-mini.py
```

The script automatically:
1. Creates a Python virtual environment
2. Installs dependencies (`grpcio`, `grpcio-reflection`, `protobuf`)
3. Launches in the venv

## Features

- **Fullscreen TUI** — Redrawing display (like `htop`), not scrolling text
- **19 Menu Options** — Information, configuration, actions, and advanced tools
- **Real-time Telemetry** — Dish status, signal, GPS, obstruction, temps
- **GPS Location** — Coordinates, altitude, speed, accuracy
- **Live Monitor** — 3-second refresh dashboard
- **Data Export** — Full JSON dump of all API responses
- **Raw gRPC** — Send custom JSON requests
- **Auto Setup** — No manual dependency installation needed
- **Fullscreen Pagination** — Long output splits across screens with "Page 1/N" navigation

## Menu Options

### Information (1–5)
- **[1] Device Info** — Hardware version, software version, serial, IDs
- **[2] Dish Status** — Uptime, signal, GPS lock, satellites, pointing angles, obstruction stats, temperature alerts
- **[3] Obstruction Map** — Visual grid showing obstructed sky regions
- **[4] Location** — GPS latitude, longitude, altitude, speed, accuracy
- **[5] Diagnostics** — Network performance, alignment, system health

### Configuration (6–10)
- **[6] View Dish Config** — Current snow melt, power save, WiFi settings
- **[7] Set Snow Melt Mode** — ON / AUTO / OFF
- **[8] Set Power Save** — Configure sleep schedule
- **[9] Set Dish Level** — Tilt mode control
- **[10] Set WiFi** — Network SSID and password

### Actions (11–12)
- **[11] Reboot Dish** — Restart the dish
- **[12] Reboot WiFi** — Restart WiFi module

### Advanced (13–19)
- **[13] Ping Dish** — Quick connectivity test
- **[14] List gRPC Methods** — All available API services
- **[15] List Request Fields** — All API request types
- **[16] Raw gRPC Request** — Send custom `{"method": {}}` JSON
- **[17] Export All Data** — Save all API responses to JSON files
- **[18] Continuous Monitor** — Live status dashboard (3s refresh)
- **[19] Reconnect** — Re-establish gRPC connection

**[q] Quit** — Exit to terminal

## Display

### Colors
- 🟢 **Green** — OK, enabled, true, yes
- 🔴 **Red** — Error, disabled, false, no
- 🟡 **Yellow** — Warning, caution
- 🔵 **Cyan** — Values, data

### Pagination
- Long content automatically splits across pages
- **Enter** — Next page
- **q** — Return to menu
- Shows "Page 1/N" at bottom

### Formatting
- "NaN" strings (from API) display as "N/A"
- Throughput in Mbps/Gbps
- Angles in degrees
- Time in days/hours/minutes/seconds
- Boolean as Yes/No

## Requirements

- **Python 3.7+**
- **Linux** (Manjaro, Ubuntu, Debian, etc.)
- **Starlink Mini dish** at `192.168.100.1:9200`
- **Network access** to dish (local network)

Dependencies auto-installed:
- `grpcio` — gRPC client
- `grpcio-reflection` — Service discovery
- `protobuf` — Message serialization

## API Connection

The script connects to your Starlink dish's gRPC API on the local network:

**Address:** `192.168.100.1:9200`  
**Auth:** None (local network is unauthenticated)  
**Discovery:** Automatic (uses gRPC reflection)

To change the address, edit line 71:
```python
DISH_ADDR = "192.168.100.1:9200"
```

## Data Types

The script displays data from real API responses:

### Device Info
- ID, hardware version, software version, serial
- Country code, UTC offset, boot count
- Build ID, generation number

### Dish Status
- Uptime, disablement code, mobility class
- Downlink/uplink throughput, latency, drop rate
- Ethernet speed, software update state
- GPS: valid, satellites, coordinates
- Pointing: azimuth, elevation, tilt angle
- Obstruction: fraction, valid samples, patches
- Alerts: heating, other warnings
- Ready states: scp, l1l2, xphy, aap, rf
- APS/PLC/UPSU stats (if available)

### Location (GPS)
- Latitude, longitude, altitude
- Source, accuracy (±meters)
- Horizontal/vertical speed
- Google Maps link

### Diagnostics
- Alignment stats: desired vs actual pointing
- Hardware self-test status
- Disablement code
- Software/hardware versions

### Obstruction Map
- 16×16 visual grid (█ = obstructed)
- Signal-to-noise ratio heatmap
- Reference frame info
- Min elevation, max theta

### History
- 72-hour network history (hourly samples)
- Downlink/uplink throughput trends
- Ping latency & drop rate history
- Recent outages with duration

### Configuration
- Snow melt mode (always on / auto / off)
- Power save: start time, duration
- Software update reboot hour
- Level dish mode
- Location requests enabled/disabled

## Keyboard

- **Enter** — Confirm, navigate pages, continue
- **q** — Return to menu (from pages or monitor)
- **Ctrl+C** — Interrupt (live monitor exits cleanly)

## Troubleshooting

**"Connection timed out"**
- Verify dish is powered on
- Verify network connectivity: `ping 192.168.100.1`
- Try option [21] to reconnect

**"No gRPC services found"**
- Dish API may be unresponsive
- Reboot dish: option [13]
- Check dish is on the same local network

**"Invalid request" / "has no field named..."**
- Your hardware version may not support that endpoint
- Check available methods: option [16]
- Try option [18] Raw gRPC to explore

**Display is garbled**
- Terminal width must be ≥80 columns
- Try resizing: `resize` or manually widen window
- Some terminals don't support ANSI codes — try `xterm`, `gnome-terminal`, or `konsole`

**Option X doesn't work**
- Some features (Stow/Unstow) require specific hardware
- Check option [16] to see if the method exists on your dish
- Not all Starlink Mini variants support all endpoints

## Keyboard Shortcuts

- **[q]** at menu — Quit
- **[q]** on any page — Return to menu
- **[Enter]** on pages — Next page / Continue
- **[Ctrl+C]** in live monitor — Stop and return to menu

## Files

```
starlink-mini.py    Main script (900+ lines)
README.md           This file
```

Optional companion scripts (separate):
- `starlink_export_all.py` — Bulk export all API data
- `starlink_discover.py` — Map all available methods
- `starlink_report.py` — Generate text reports

## Notes

- **No internet required** — All communication is local
- **No authentication** — Local gRPC API is open
- **Stateless** — Script doesn't store any configuration
- **Read-mostly** — Some operations (config changes) send commands
- **Safe** — Operations ask for confirmation before running

## Version

- **Script:** v1.0
- **API Version:** 42 (Starlink Mini)
- **Tested On:** Manjaro Linux, Python 3.9+
- **Updated:** 2026-04-10

---

**Usage:** `./starlink-mini.py`  
**Quit:** Press `q` or Ctrl+C
