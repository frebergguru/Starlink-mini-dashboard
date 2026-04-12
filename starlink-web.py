#!/usr/bin/env python3
"""
STARLINK MINI · WEB UI
Serves a browser dashboard that proxies the dish's gRPC API.

Run:
  ./starlink-web.py          # http://127.0.0.1:8800
  ./starlink-web.py --host 0.0.0.0 --port 8800
"""

import os, sys, json, io, contextlib, argparse, importlib.util, subprocess, venv, shutil, threading, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
MINI_PATH = os.path.join(SCRIPT_DIR, "starlink-mini.py")


def setup_venv():
    venv_dir = os.path.join(SCRIPT_DIR, "venv")
    in_venv = hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    if in_venv:
        return
    if not os.path.exists(venv_dir):
        print(f"Creating virtual environment in {venv_dir}...")
        venv.create(venv_dir, with_pip=True)
    pip_exe = os.path.join(venv_dir, "bin", "pip")
    if not os.path.exists(pip_exe):
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    print("Ensuring dependencies...")
    subprocess.check_call([pip_exe, "install", "-q", "--timeout", "120",
                           "grpcio>=1.62.0", "grpcio-reflection>=1.62.0", "protobuf>=4.25.0",
                           "segno>=1.5.0"])
    python_exe = os.path.join(venv_dir, "bin", "python")
    if not os.path.exists(python_exe):
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
    os.execv(python_exe, [python_exe, __file__] + sys.argv[1:])


setup_venv()


def _load_mini():
    spec = importlib.util.spec_from_file_location("starlink_mini", MINI_PATH)
    mod = importlib.util.module_from_spec(spec)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spec.loader.exec_module(mod)
    return mod


MINI = _load_mini()
DishClient = MINI.DishClient
DISH_ADDR = MINI.DISH_ADDR
ROUTER_ADDR = "192.168.1.1:9000"

# Extend the permitted gRPC keys with additional read-only endpoints used by
# the dish and the Starlink router.
DishClient.PERMITTED_KEYS = frozenset(
    DishClient.PERMITTED_KEYS
    | {
        # Dish / router reads
        "wifi_get_clients",
        "wifi_get_config",
        "wifi_guest_info",
        "wifi_self_test",
        "wifi_get_client_history",
        "get_network_interfaces",
        "get_radio_stats",
        "get_ping",
    }
)


class DishProxy:
    def __init__(self, addr):
        self.addr = addr
        self.client = DishClient(addr)
        self.lock = threading.Lock()
        self.connected = False
        self.connect_msg = ""

    def connect(self):
        with self.lock:
            ok, msg = self.client.connect()
            self.connected = ok
            self.connect_msg = msg
            return ok, msg

    def reconnect(self):
        with self.lock:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = DishClient(self.addr)
        return self.connect()

    def request(self, key, body=None):
        """Send a request and surface all gRPC errors (including PERMISSION_DENIED
        and UNIMPLEMENTED, which the underlying DishClient silently swallows)."""
        import grpc
        from google.protobuf import json_format
        with self.lock:
            if not self.connected:
                return None, "not connected"
            if key not in DishClient.PERMITTED_KEYS:
                return None, f"request key '{key}' not permitted"
            try:
                path, req_tn, resp_tn = self.client._find_handle()
                rc = self.client._get_msg_class(req_tn)
                rsc = self.client._get_msg_class(resp_tn)
                if not rc or not rsc:
                    return None, "cannot resolve protobuf types"
                req = rc()
                try:
                    json_format.Parse(json.dumps({key: body or {}}), req, ignore_unknown_fields=False)
                except Exception as e:
                    return None, f"invalid request: {e}"
                resp = self.client.channel.unary_unary(
                    path,
                    request_serializer=lambda mm: mm.SerializeToString(),
                    response_deserializer=rsc.FromString,
                )(req, timeout=12)
                return json_format.MessageToDict(resp, preserving_proto_field_name=True), ""
            except grpc.RpcError as e:
                code = e.code().name if hasattr(e.code(), "name") else str(e.code())
                return None, f"{code}: {(e.details() or '').strip()}"
            except Exception as e:
                return None, str(e)

    def services(self):
        with self.lock:
            return self.client.list_all_services()

    def fields(self):
        with self.lock:
            return self.client.list_request_fields()


DISH_PROXY = DishProxy(DISH_ADDR)
ROUTER_PROXY = DishProxy(ROUTER_ADDR)
PROXY = DISH_PROXY  # backward-compat alias for existing references


def _json_response(handler, code, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


SENSITIVE_KEYS = {"password", "pw", "secret", "token", "credential", "client_key", "basic_service_set_psk"}


def _scrub(obj):
    """Recursively redact sensitive fields (passwords, etc.) before sending to browser."""
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def _extract_wifi_secrets(data):
    """Pull (ssid, psk, auth) tuples from a wifi_get_config response. Skips
    networks that have no usable passphrase (open networks, empty strings)."""
    root = data or {}
    root = root.get("wifi_get_config") or root.get("wifiGetConfig") or root
    cfg = root.get("wifi_config") or root.get("wifiConfig") or root
    out = []
    for net in (cfg.get("networks") or []):
        ssid = None
        psk = None
        auth = None
        is_guest = bool(net.get("guest"))
        for bss in (net.get("basic_service_sets") or []):
            if bss.get("ssid") and not ssid:
                ssid = bss.get("ssid")
            if bss.get("basic_service_set_psk") and not psk:
                psk = bss.get("basic_service_set_psk")
            cand_auth = bss.get("auth_type") or bss.get("authType")
            if cand_auth and not auth:
                auth = cand_auth
        if ssid and psk:
            out.append({"ssid": ssid, "psk": psk, "auth": auth or "WPA2", "guest": is_guest})
    return out


def _wifi_uri(ssid, psk, auth="WPA", hidden=False):
    """Build a Wi-Fi URI (RFC-style used by most QR scanners) with escaping."""
    def esc(s):
        return "".join("\\" + c if c in "\\;,:\"" else c for c in str(s))
    a = (auth or "WPA").upper()
    if a in ("OPEN", "NONE", ""):
        return f"WIFI:T:nopass;S:{esc(ssid)};;"
    # Starlink reports auth_type like "AUTH_TYPE_WPA2_PSK" — map to the bare
    # forms scanners understand.
    if "SAE" in a or "WPA3" in a:
        a = "WPA"  # WPA3-SAE falls back to WPA on older scanners; most accept it.
    elif "WPA" in a:
        a = "WPA"
    elif "WEP" in a:
        a = "WEP"
    else:
        a = "WPA"
    return f"WIFI:T:{a};S:{esc(ssid)};P:{esc(psk)};{'H:true;' if hidden else ''};"


DISH_KEY_MAP = {
    "/api/status": "getStatus",
    "/api/device": "getDeviceInfo",
    "/api/location": "getLocation",
    "/api/diagnostics": "getDiagnostics",
    "/api/config": "dishGetConfig",
    "/api/obstruction": "dishGetObstructionMap",
    "/api/history": "getHistory",
    "/api/transceiver/status": "transceiver_get_status",
    "/api/transceiver/telemetry": "transceiver_get_telemetry",
    "/api/clients": "wifi_get_clients",  # legacy — now best hit via router
}

ROUTER_KEY_MAP = {
    "/api/router/status": "get_status",
    "/api/router/device": "get_device_info",
    "/api/router/diagnostics": "get_diagnostics",
    "/api/router/history": "get_history",
    "/api/router/clients": "wifi_get_clients",
    "/api/router/client_history": "wifi_get_client_history",
    "/api/router/config": "wifi_get_config",
    "/api/router/guest": "wifi_guest_info",
    "/api/router/self_test": "wifi_self_test",
    "/api/router/network_interfaces": "get_network_interfaces",
    "/api/router/radio_stats": "get_radio_stats",
    "/api/router/ping": "get_ping",
}


def _proxy_fetch(proxy, grpc_key, scrub=False):
    if not proxy.connected:
        return 503, {"error": "not connected", "message": proxy.connect_msg}
    data, err = proxy.request(grpc_key)
    if data is None:
        return 502, {"error": "request failed", "detail": err or "no data"}
    if scrub:
        data = _scrub(data)
    return 200, {"data": data}


def _api_get(path):
    if path == "/api/state":
        return 200, {
            "dish": {
                "connected": DISH_PROXY.connected,
                "message": DISH_PROXY.connect_msg,
                "address": DISH_ADDR,
            },
            "router": {
                "connected": ROUTER_PROXY.connected,
                "message": ROUTER_PROXY.connect_msg,
                "address": ROUTER_ADDR,
            },
        }

    if path in DISH_KEY_MAP:
        return _proxy_fetch(DISH_PROXY, DISH_KEY_MAP[path])

    if path in ROUTER_KEY_MAP:
        needs_scrub = path == "/api/router/config"
        return _proxy_fetch(ROUTER_PROXY, ROUTER_KEY_MAP[path], scrub=needs_scrub)

    if path == "/api/router/wifi_secrets":
        if not ROUTER_PROXY.connected:
            return 503, {"error": "not connected", "message": ROUTER_PROXY.connect_msg}
        data, err = ROUTER_PROXY.request("wifi_get_config")
        if data is None:
            return 502, {"error": "request failed", "detail": err or "no data"}
        return 200, {"networks": _extract_wifi_secrets(data), "_raw": data}

    if path == "/api/services":
        if not DISH_PROXY.connected:
            return 503, {"error": "not connected"}
        return 200, {"data": DISH_PROXY.services()}

    if path == "/api/fields":
        if not DISH_PROXY.connected:
            return 503, {"error": "not connected"}
        return 200, {"data": [{"name": n, "type": str(t)} for n, t in DISH_PROXY.fields()]}

    if path == "/api/ping":
        ping_bin = shutil.which("ping") or "/bin/ping"
        try:
            r = subprocess.run([ping_bin, "-c", "5", "-i", "0.3", "192.168.100.1"],
                               capture_output=True, text=True, timeout=15)
            return 200, {"output": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
        except Exception as e:
            return 500, {"error": str(e)}

    return 404, {"error": "not found"}


def _api_post(path, body):
    if path == "/api/reconnect":
        ok, msg = DISH_PROXY.reconnect()
        return (200 if ok else 502), {"connected": ok, "message": msg}

    if path == "/api/router/reconnect":
        ok, msg = ROUTER_PROXY.reconnect()
        return (200 if ok else 502), {"connected": ok, "message": msg}

    if not DISH_PROXY.connected:
        return 503, {"error": "not connected"}

    if path == "/api/reboot":
        data, err = DISH_PROXY.request("reboot")
        return 200, {"data": data, "detail": err}

    if path == "/api/raw":
        key = (body or {}).get("key", "")
        payload = (body or {}).get("payload") or {}
        if not key:
            return 400, {"error": "key required"}
        data, err = PROXY.request(key, payload)
        if data is None:
            return 502, {"error": "request failed", "detail": err}
        return 200, {"data": data}

    return 404, {"error": "not found"}


def _safe_static_path(url_path):
    if url_path in ("", "/", "/index.html"):
        return os.path.join(STATIC_DIR, "index.html")
    if url_path.startswith("/static/"):
        rel = url_path[len("/static/"):]
    else:
        rel = url_path.lstrip("/")
    if not rel:
        return None
    full = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if not full.startswith(STATIC_DIR + os.sep):
        return None
    return full


class Handler(BaseHTTPRequestHandler):
    server_version = "StarlinkMiniWeb/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.address_string()} - {fmt % args}\n")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > 64 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _serve_wifi_qr(self, url):
        from urllib.parse import parse_qs
        qs = parse_qs(url.query or "")
        target_ssid = (qs.get("ssid") or [""])[0]
        if not target_ssid:
            _json_response(self, 400, {"error": "ssid query parameter required"})
            return
        if not ROUTER_PROXY.connected:
            _json_response(self, 503, {"error": "not connected", "message": ROUTER_PROXY.connect_msg})
            return
        data, err = ROUTER_PROXY.request("wifi_get_config")
        if data is None:
            _json_response(self, 502, {"error": "request failed", "detail": err or "no data"})
            return
        match = next((n for n in _extract_wifi_secrets(data) if n["ssid"] == target_ssid), None)
        if not match:
            _json_response(self, 404, {"error": "ssid not found"})
            return
        try:
            import segno
        except Exception as e:
            _json_response(self, 500, {"error": f"segno unavailable: {e}"})
            return
        uri = _wifi_uri(match["ssid"], match["psk"], match.get("auth"), hidden=False)
        qr = segno.make(uri, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=10, border=2, dark="#0b0d12", light="#ffffff")
        svg = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(svg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(svg)

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/api/router/wifi_qr":
            self._serve_wifi_qr(url)
            return
        if path.startswith("/api/"):
            code, payload = _api_get(path)
            _json_response(self, code, payload)
            return
        full = _safe_static_path(path)
        if not full or not os.path.isfile(full):
            self.send_error(404, "Not found")
            return
        ctype, _ = mimetypes.guess_type(full)
        ctype = ctype or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(500, "Read error")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            self.send_error(404, "Not found")
            return
        body = self._read_json()
        if body is None:
            _json_response(self, 400, {"error": "invalid or oversized JSON"})
            return
        code, payload = _api_post(url.path, body)
        _json_response(self, code, payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    args = ap.parse_args()

    print(f"  Connecting to dish at {DISH_ADDR} ...")
    ok, msg = DISH_PROXY.connect()
    if ok:
        print(f"  Dish connected — {len(DISH_PROXY.client._services)} service(s)")
    else:
        print(f"  Dish not connected: {msg}")

    print(f"  Connecting to router at {ROUTER_ADDR} ...")
    ok, msg = ROUTER_PROXY.connect()
    if ok:
        print(f"  Router connected — {len(ROUTER_PROXY.client._services)} service(s)")
    else:
        print(f"  Router not connected: {msg}")
        print(f"  (Router tab will be empty; use the Reconnect button.)")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"\n  Serving Starlink Web UI on {url}")
    print(f"  Ctrl+C to stop\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping...")
    finally:
        srv.server_close()
        for p in (DISH_PROXY, ROUTER_PROXY):
            try:
                p.client.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
