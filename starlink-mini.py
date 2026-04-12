#!/usr/bin/env python3
"""
STARLINK MINI · CONTROL CENTER
Terminal dashboard mirroring starlink-web.py's feature set.

Talks to both the dish (192.168.100.1:9200) and the router (192.168.1.1:9000)
over gRPC. Read-only except for a single write — rebooting the dish. All
config writers (snow-melt, power-save, level, Wi-Fi) were removed so this
stays in lockstep with the web dashboard, which is also read-only.

Install:
  chmod +x starlink-mini.py
  ./starlink-mini.py

The script will automatically set up a virtual environment and install dependencies.
"""

import json, subprocess, sys, os, shutil, time, venv, math, io, contextlib, importlib.util
from datetime import datetime

REQUIRED_DEPS = [
    ("grpc", "grpcio>=1.62.0"),
    ("grpc_reflection", "grpcio-reflection>=1.62.0"),
    ("google.protobuf", "protobuf>=4.25.0"),
]


def _venv_bin(venv_dir, name):
    exe = os.path.join(venv_dir, "bin", name)
    if os.path.exists(exe):
        return exe
    return os.path.join(venv_dir, "Scripts", f"{name}.exe")


def setup_venv():
    """Create the project venv if needed, re-exec into it, then install any missing deps."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(script_dir, "venv")
    venv_python = _venv_bin(venv_dir, "python")

    if os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        if not os.path.exists(venv_dir):
            print(f"Creating virtual environment in {venv_dir}...")
            venv.create(venv_dir, with_pip=True)
        venv_python = _venv_bin(venv_dir, "python")
        print("Launching in virtual environment...\n")
        os.execv(venv_python, [venv_python, __file__] + sys.argv[1:])

    def _missing(mod):
        try:
            return importlib.util.find_spec(mod) is None
        except (ModuleNotFoundError, ValueError):
            return True
    missing = [spec for mod, spec in REQUIRED_DEPS if _missing(mod)]
    if missing:
        pip_exe = _venv_bin(venv_dir, "pip")
        print(f"Installing missing dependencies: {', '.join(missing)}")
        subprocess.check_call([pip_exe, "install", "-q", "--timeout", "120", *missing])

setup_venv()

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool as dp, json_format, message_factory
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

DISH_ADDR = "192.168.100.1:9200"
ROUTER_ADDR = "192.168.1.1:9000"

# Sensitive field names redacted from any raw JSON display. Parity with
# starlink-web.py's SENSITIVE_KEYS so passwords never hit the terminal.
SENSITIVE_KEYS = frozenset({
    "password", "pw", "secret", "token", "credential", "client_key",
    "basic_service_set_psk",
})

class C:
    RST="\033[0m";BOLD="\033[1m";DIM="\033[2m"
    RED="\033[31m";GRN="\033[32m";YEL="\033[33m";CYN="\033[36m"
    BRED="\033[91m";BGRN="\033[92m";BYEL="\033[93m"
    BBLU="\033[94m";BCYN="\033[96m";BWHT="\033[97m"

def clear(): sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()
def tw(): return shutil.get_terminal_size((80,24)).columns
def th(): return shutil.get_terminal_size((80,24)).lines
def center(t,w=None): return t.center(w or tw())
def hide_cursor(): sys.stdout.write("\033[?25l"); sys.stdout.flush()
def show_cursor(): sys.stdout.write("\033[?25h"); sys.stdout.flush()
def enter_alt(): sys.stdout.write("\033[?1049h\033[?25l"); sys.stdout.flush()
def exit_alt(): sys.stdout.write("\033[?25h\033[?1049l"); sys.stdout.flush()
def hr(ch="─",co=C.DIM): print(f"{co}{ch*tw()}{C.RST}")
def pause():
    print()
    input(f"  {C.DIM}Press Enter to continue …{C.RST}")

@contextlib.contextmanager
def capture_lines():
    """Redirect stdout to a StringIO buffer. Yield the buffer."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf

def render_screen(lines, status=""):
    """
    Clear screen, print banner, render lines, paginate if needed.
    Waits for Enter (or 'q' to quit) before returning.
    """
    h = th()
    usable = max(4, h - 8)  # reserve 8 rows: banner(6) + hr(1) + status(1)
    hide_cursor()
    pages = [lines[i:i+usable] for i in range(0, max(1, len(lines)), usable)]
    for i, page in enumerate(pages):
        clear()
        banner()
        for l in page:
            print(l)
        # pad remaining rows
        for _ in range(usable - len(page)):
            print()
        hr("─", C.DIM)
        show_cursor()
        try:
            if len(pages) == 1:
                print(f"  {C.DIM}{status or 'Press Enter to return …'}{C.RST}", end="", flush=True)
                input()
            else:
                raw = input(f"  {C.DIM}Page {i+1}/{len(pages)} — Enter: next  q: menu{C.RST}  ").strip().lower()
                hide_cursor()
                if raw == "q":
                    break
        finally:
            show_cursor()

def confirm(p):
    return input(f"  {C.YEL}⚠  {p} [y/N]: {C.RST}").strip().lower() in ("y","yes")

def banner():
    w=tw(); print()
    print(f"{C.BCYN}{C.BOLD}{center('┌'+'─'*50+'┐',w)}{C.RST}")
    print(f"{C.BCYN}{C.BOLD}{center('│      ✦  STARLINK MINI · CONTROL CENTER  ✦      │',w)}{C.RST}")
    print(f"{C.BCYN}{C.BOLD}{center('└'+'─'*50+'┘',w)}{C.RST}")
    print(f"{C.DIM}{center('gRPC · '+DISH_ADDR,w)}{C.RST}"); print()

def ps(msg,ok=True):
    icon=f"{C.BGRN}✔{C.RST}" if ok else f"{C.BRED}✘{C.RST}"
    print(f"  {icon}  {msg}")

def sec(t):
    print(); print(f"  {C.BCYN}{C.BOLD}┌─ {t}{C.RST}"); print(f"  {C.BCYN}│{C.RST}")
def pf(l,v,ind=1):
    pad="   "*ind; lbl=f"{C.BWHT}{l}{C.RST}"
    # Handle NaN strings (API may return "NaN" as string instead of null)
    if isinstance(v, str) and v.lower() == "nan":
        v = None
    if v is None or v=="" or v=="N/A": val=f"{C.DIM}N/A{C.RST}"
    elif isinstance(v,bool): val=f"{C.BGRN}Yes{C.RST}" if v else f"{C.DIM}No{C.RST}"
    else: val=f"{C.CYN}{v}{C.RST}"
    print(f"  {C.BCYN}│{C.RST}  {pad}{lbl}: {val}")
def sec_end(): print(f"  {C.BCYN}└{'─'*48}{C.RST}")

def sg(d,*keys,default=None):
    for k in keys:
        if isinstance(d,dict): d=d.get(k,default)
        else: return default
    return d if d is not None else default

def sg_coalesce(d, key_pairs, default=None):
    """Safely coalesce multiple key names, checking for None explicitly (not falsy)."""
    for key in key_pairs:
        val = sg(d, key, default=None)
        if val is not None: return val
    return default

def fmt_up(s):
    if not s: return "N/A"
    s=int(float(s)); d,s=divmod(s,86400); h,s=divmod(s,3600); m,s=divmod(s,60)
    p=[];
    if d: p.append(f"{d}d")
    if h: p.append(f"{h}h")
    if m: p.append(f"{m}m")
    p.append(f"{s}s"); return " ".join(p)

def fmt_bps(b):
    if not b: return "0 bps"
    b=float(b)
    if math.isnan(b) or math.isinf(b): return "N/A"
    for u in ["bps","Kbps","Mbps","Gbps"]:
        if abs(b)<1000: return f"{b:,.1f} {u}"
        b/=1000
    return f"{b:,.1f} Tbps"

def fmt_pct(v):
    if v is None: return "N/A"
    v=float(v)
    if math.isnan(v) or math.isinf(v): return "N/A"
    return f"{v*100:.1f}%" if v<=1 else f"{v:.1f}%"

def fmt_snr(v):
    """SNR as reported by the LAN gRPC API: current firmware returns a
    boolean `is_snr_above_noise_floor`, older firmwares returned a numeric
    dB value. Accept both and format accordingly."""
    if v is None:
        return "N/A"
    if isinstance(v, bool):
        return "Above floor" if v else "Below floor"
    try:
        return f"{float(v):.1f} dB"
    except (TypeError, ValueError):
        return "N/A"

def fmt_deg(v):
    return f"{float(v):.2f}°" if v is not None else "N/A"


class DishClient:
    # Whitelist of permitted gRPC request keys. Matches the parity set used by
    # starlink-web.py — all reads plus `reboot`, nothing else. Dish-side keys
    # are separate from router-side keys listed further below.
    PERMITTED_KEYS=frozenset([
        # Dish reads
        "getDeviceInfo","get_device_info","getStatus","get_status",
        "dishGetObstructionMap","dish_get_obstruction_map",
        "getDiagnostics","get_diagnostics",
        "getLocation","get_location",
        "dishGetConfig","dish_get_config",
        "dishGetHistoryStats","dish_get_history_stats","getHistory","get_history",
        "transceiver_get_status","transceiver_get_telemetry",
        # Router reads (used when this client is pointed at the router endpoint)
        "wifi_get_clients","wifi_get_config","wifi_guest_info","wifi_self_test",
        "wifi_get_client_history","get_network_interfaces","get_radio_stats","get_ping",
        # Single write: dish reboot
        "reboot",
    ])

    def __init__(self, addr=DISH_ADDR):
        self.addr=addr; self.channel=None; self._pool=dp.DescriptorPool()
        self._connected=False; self._services=[]; self._loaded_files=set()

    def connect(self):
        try:
            opts=[("grpc.max_receive_message_length",50*1024*1024),
                  ("grpc.connect_timeout_ms",8000)]
            # Starlink dish on local LAN does not support TLS — use insecure channel
            self.channel=grpc.insecure_channel(self.addr, options=opts)
            try: grpc.channel_ready_future(self.channel).result(timeout=6)
            except grpc.FutureTimeoutError:
                return False,"Connection timed out — is the dish reachable at "+self.addr+"?"
            self._rstub=reflection_pb2_grpc.ServerReflectionStub(self.channel)
            self._services=self._list_svcs()
            if not self._services: return False,"No gRPC services found."
            self._load_all_descriptors()
            _, req_tn, resp_tn = self._find_handle()
            if not self._get_msg_class(req_tn) or not self._get_msg_class(resp_tn):
                return False,"Failed to load Request/Response protobuf messages from reflection."
            self._connected=True; return True,"OK"
        except Exception as e: return False,str(e)

    def _list_svcs(self):
        req=reflection_pb2.ServerReflectionRequest(list_services="")
        svcs=[]
        for r in self._rstub.ServerReflectionInfo(iter([req])):
            if r.HasField("list_services_response"):
                svcs=[s.name for s in r.list_services_response.service]
        return svcs

    def _load_file_by_symbol(self, sym, depth=0):
        if depth > 50:  # Prevent stack overflow from deep dependency chains
            return
        if sym in self._loaded_files: return
        req=reflection_pb2.ServerReflectionRequest(file_containing_symbol=sym)
        loaded_any=False
        try:
            for r in self._rstub.ServerReflectionInfo(iter([req])):
                if r.HasField("file_descriptor_response"):
                    for fb in r.file_descriptor_response.file_descriptor_proto:
                        if len(fb) > 1024*1024:  # Reject descriptors > 1MB
                            continue
                        fd=descriptor_pb2.FileDescriptorProto(); fd.ParseFromString(fb)
                        if fd.name and fd.name.startswith("google.protobuf"):
                            # Validate we're loading expected proto files
                            pass
                        for dep in fd.dependency: self._load_file_by_name(dep, depth+1)
                        try:
                            self._pool.Add(fd)
                            loaded_any=True
                        except Exception as e:
                            pass
            if loaded_any: self._loaded_files.add(sym)
        except Exception as e:
            pass

    def _load_file_by_name(self, fn, depth=0):
        if depth > 50:  # Prevent stack overflow from deep dependency chains
            return
        if fn in self._loaded_files: return
        self._loaded_files.add(fn)  # Mark as loading before recursing to prevent cycles
        req=reflection_pb2.ServerReflectionRequest(file_by_filename=fn)
        loaded_any=False
        try:
            for r in self._rstub.ServerReflectionInfo(iter([req])):
                if r.HasField("file_descriptor_response"):
                    for fb in r.file_descriptor_response.file_descriptor_proto:
                        if len(fb) > 1024*1024:  # Reject descriptors > 1MB
                            continue
                        fd=descriptor_pb2.FileDescriptorProto(); fd.ParseFromString(fb)
                        for dep in fd.dependency: self._load_file_by_name(dep, depth+1)
                        try:
                            self._pool.Add(fd)
                            loaded_any=True
                        except Exception as e:
                            pass
        except Exception as e:
            pass

    def _load_all_descriptors(self):
        for s in self._services: self._load_file_by_symbol(s)

    def _get_msg_class(self, name):
        try:
            desc=self._pool.FindMessageTypeByName(name)
            try: return message_factory.GetMessageClass(desc)
            except (AttributeError, TypeError):
                f=message_factory.MessageFactory(pool=self._pool)
                return f.GetPrototype(desc)
        except Exception:
            return None

    def _find_handle(self):
        found_service=None
        found_method=None
        for sn in self._services:
            try:
                sd=self._pool.FindServiceByName(sn)
                found_service=sn
                for m in sd.methods:
                    if m.name=="Handle":
                        found_method=m
                        return f"/{sn}/Handle", m.input_type.full_name, m.output_type.full_name
            except Exception: continue
        return "/SpaceX.API.Device.Device/Handle","SpaceX.API.Device.Request","SpaceX.API.Device.Response"

    def request(self, key, body=None):
        if not self._connected:
            print(f"  {C.BRED}Not connected.{C.RST}"); return None
        # Validate key against whitelist for security
        if key not in self.PERMITTED_KEYS:
            print(f"  {C.BRED}Request key '{key}' is not permitted.{C.RST}"); return None
        try:
            path, req_tn, resp_tn = self._find_handle()
            rc=self._get_msg_class(req_tn); rsc=self._get_msg_class(resp_tn)
            if not rc or not rsc:
                print(f"  {C.BRED}Cannot resolve protobuf types.{C.RST}"); return None
            req=rc()
            payload={key: body or {}}
            try:
                json_format.Parse(json.dumps(payload), req, ignore_unknown_fields=False)
            except Exception as e:
                print(f"  {C.BRED}Invalid request: {e}{C.RST}"); return None

            resp=self.channel.unary_unary(path,
                request_serializer=lambda m: m.SerializeToString(),
                response_deserializer=rsc.FromString)(req, timeout=12)
            return json_format.MessageToDict(resp, preserving_proto_field_name=True)
        except grpc.RpcError as e:
            code_name = e.code().name if hasattr(e.code(), 'name') else str(e.code())
            details = (e.details() or "(no details)")[:200]  # Truncate error details for security
            if code_name not in ("PERMISSION_DENIED", "UNIMPLEMENTED"):
                print(f"  {C.BRED}gRPC Error [{code_name}]: {details}{C.RST}")
            return None
        except Exception as e:
            print(f"  {C.BRED}Error: {e}{C.RST}"); return None

    def list_all_services(self):
        r={}
        for sn in self._services:
            try:
                sd=self._pool.FindServiceByName(sn)
                r[sn]=[m.name for m in sd.methods]
            except Exception: r[sn]=["?"]
        return r

    def list_request_fields(self):
        _,req_tn,_=self._find_handle()
        try:
            desc=self._pool.FindMessageTypeByName(req_tn)
            return [(f.name, f.message_type.full_name if f.message_type else str(f.type)) for f in desc.fields]
        except Exception: return []

    def close(self):
        if self.channel: self.channel.close()


# ── Display ───────────────────────────────────────────────────

def display_device_info(data):
    info=sg(data,"dish_get_status") or sg(data,"dishGetStatus") or sg(data,"get_device_info") or sg(data,"getDeviceInfo") or data
    di=sg(info,"device_info") or sg(info,"deviceInfo") or info
    sec("DEVICE INFO")
    for k in ["id","hardware_version","hardwareVersion","software_version","softwareVersion",
              "country_code","countryCode","utc_offset_s","utcOffsetS","board_rev","boardRev",
              "manufactured_version","manufacturedVersion","generation_number","generationNumber",
              "boot_count","bootcount","is_dev","isDev","anti_rollback_version","antiRollbackVersion",
              "dish_cohoused","dishCohoused"]:
        v=sg(di,k)
        if v is not None: pf(k.replace("_"," ").title(), v)
    sec_end()

def display_status(data, diag=None):
    s=sg(data,"getStatus") or sg(data,"dish_get_status") or sg(data,"dishGetStatus") or data

    sec("DEVICE INFO")
    di=sg_coalesce(s, ["device_info","deviceInfo"], default={})
    if di:
        for k in ["id","hardware_version","hardwareVersion","software_version","softwareVersion",
                  "country_code","countryCode","bootcount","generation_number","build_id"]:
            v=sg(di,k)
            if v is not None: pf(k.replace("_"," ").title(),v)
    # Hardware self-test lives on the diagnostics endpoint, fetched alongside
    # status by the menu handler.
    if diag:
        dg=sg(diag,"dish_get_diagnostics") or sg(diag,"dishGetDiagnostics") or diag
        hst=sg(dg,"hardware_self_test") or sg(dg,"hardwareSelfTest")
        if hst:
            col=C.BGRN if hst == "PASSED" else C.BRED if hst == "FAILED" else C.CYN
            pf("Hardware Self-Test",f"{col}{hst}{C.RST}")
    sec_end()

    sec("DISH STATUS")
    disablement=sg(s,"disablement_code") or "UNKNOWN"
    mobility=sg(s,"mobility_class") or "UNKNOWN"
    sc=C.BGRN if disablement == "OKAY" else C.BYEL
    pf("Disablement",f"{sc}{disablement}{C.RST}")
    pf("Mobility Class",mobility)
    uptime=sg(s,"device_state","uptime_s") or sg(s,"deviceState","uptimeS")
    pf("Uptime",fmt_up(uptime))
    alerts=sg(s,"alerts") or {}
    active=[k for k,v in alerts.items() if v] if isinstance(alerts,dict) else []
    heating=bool(alerts.get("is_heating") or alerts.get("dish_is_heating")) if isinstance(alerts,dict) else False
    if heating:
        pf("Snow Melt",f"{C.BYEL}♨ heating now{C.RST}")
    pf("Alerts","")
    if active:
        for a in active: print(f"  {C.BCYN}│{C.RST}       {C.BYEL}⚠  {a}{C.RST}")
    else: print(f"  {C.BCYN}│{C.RST}       {C.BGRN}None{C.RST}")
    sec_end()

    outage=sg(s,"outage") or {}
    if outage:
        sec("CURRENT OUTAGE")
        for k,v in outage.items(): pf(k.replace("_"," ").title(),v)
        sec_end()

    sec("NETWORK & THROUGHPUT")
    dl=sg_coalesce(s, ["downlink_throughput_bps","downlinkThroughputBps"])
    ul=sg_coalesce(s, ["uplink_throughput_bps","uplinkThroughputBps"])
    lat=sg_coalesce(s, ["pop_ping_latency_ms","popPingLatencyMs"])
    drop=sg_coalesce(s, ["pop_ping_drop_rate","popPingDropRate"])
    eth=sg_coalesce(s, ["eth_speed_mbps","ethSpeedMbps"])
    pf("Downlink",fmt_bps(dl)); pf("Uplink",fmt_bps(ul))
    pf("Latency",f"{lat} ms" if lat else "N/A"); pf("Drop Rate",fmt_pct(drop))
    if eth: pf("Ethernet Speed",f"{eth} Mbps")
    sec_end()

    sec("GPS & POINTING")
    gps=sg_coalesce(s, ["gps_stats","gpsStats"], default={})
    for k,v in gps.items(): pf(k.replace("_"," ").title(),v)
    for key in ["tilt_angle_deg","tiltAngleDeg","boresight_azimuth_deg","boresightAzimuthDeg",
                "boresight_elevation_deg","boresightElevationDeg"]:
        v=sg(s,key)
        if v is not None: pf(key.replace("_"," ").title(),fmt_deg(v))
    slot=sg_coalesce(s, ["seconds_to_first_nonempty_slot"])
    if slot: pf("Seconds To First Slot",slot)
    align=sg_coalesce(s, ["alignment_stats","alignmentStats"], default={})
    if align:
        pf("Alignment Stats","")
        for k,v in align.items():
            if "deg" in k.lower(): pf(f"  {k.replace('_',' ').title()}",fmt_deg(v) if isinstance(v,(int,float)) else v)
            else: pf(f"  {k.replace('_',' ').title()}",v)
    sec_end()

    routers=sg_coalesce(s, ["connected_routers"], default={})
    if routers and isinstance(routers, dict) and any(routers.values()):
        sec("CONNECTED ROUTERS")
        for k,v in routers.items(): pf(k.replace("_"," ").title(),v)
        sec_end()

    sec("SIGNAL QUALITY")
    snr=sg_coalesce(s, ["is_snr_above_noise_floor","snr_above_noise_floor","snrAboveNoiseFloor"])
    pf("SNR Above Floor",fmt_snr(snr))
    rs=sg_coalesce(s, ["ready_states","readyStates"], default={})
    if rs:
        pf("Ready States","")
        for k,v in rs.items(): print(f"  {C.BCYN}│{C.RST}    {C.CYN}{k:20}{C.RST} {C.BGRN if v else C.BRED}{v}{C.RST}")
    for key in ["class_of_service","classOfService","software_update_state","softwareUpdateState"]:
        v=sg(s,key)
        if v is not None: pf(key.replace("_"," ").title(),v)
    has_act=sg_coalesce(s, ["has_actuators"])
    if has_act: pf("Has Actuators",has_act)
    has_cal=sg_coalesce(s, ["has_signed_cals"])
    if has_cal: pf("Has Signed Cals",has_cal)
    sec_end()

    sec("BANDWIDTH & RESTRICTIONS")
    dl_restrict=sg_coalesce(s, ["dl_bandwidth_restricted_reason"])
    ul_restrict=sg_coalesce(s, ["ul_bandwidth_restricted_reason"])
    if dl_restrict: pf("Downlink Restricted",dl_restrict)
    if ul_restrict: pf("Uplink Restricted",ul_restrict)
    acct=sg_coalesce(s, ["account_shard"])
    if acct: pf("Account Shard",acct)
    sec_end()

    sec("SYSTEM & TIMERS")
    init_dur=sg_coalesce(s, ["initialization_duration_seconds"])
    if init_dur and isinstance(init_dur, (int,float)): pf("Init Duration",f"{float(init_dur):.1f} seconds")
    reboot_time=sg_coalesce(s, ["seconds_until_swupdate_reboot_possible"])
    if reboot_time and isinstance(reboot_time, (int,float)): pf("Software Update Reboot In",f"{float(reboot_time):.0f} seconds")
    sec_end()

    aps=sg_coalesce(s, ["aps_stats"], default={})
    if aps and isinstance(aps, dict) and any(aps.values()):
        sec("APS STATS")
        for k,v in aps.items(): pf(k.replace("_"," ").title(),v)
        sec_end()

    plc=sg_coalesce(s, ["plc_stats"], default={})
    if plc and isinstance(plc, dict) and any(plc.values()):
        sec("PLC STATS")
        for k,v in plc.items(): pf(k.replace("_"," ").title(),v)
        sec_end()

    upsu=sg_coalesce(s, ["upsu_stats"], default={})
    if upsu and isinstance(upsu, dict) and any(upsu.values()):
        sec("UPSU STATS")
        for k,v in upsu.items(): pf(k.replace("_"," ").title(),v)
        sec_end()

    obs=sg_coalesce(s, ["obstruction_stats","obstructionStats"], default={})
    if obs:
        sec("OBSTRUCTION")
        for k,v in obs.items():
            if isinstance(v,list):
                pf(k.replace("_"," ").title(),f"[{len(v)} wedges]")
                for i,w in enumerate(v): print(f"  {C.BCYN}│{C.RST}      wedge {i}: {C.CYN}{fmt_pct(w)}{C.RST}")
            elif "fraction" in k.lower(): pf(k.replace("_"," ").title(),fmt_pct(v) if isinstance(v,(int,float)) else v)
            else:
                pf(k.replace("_"," ").title(),v)
        sec_end()

def display_location(data):
    # Try getLocation endpoint first
    loc=sg(data,"get_location") or sg(data,"getLocation") or {}

    if not loc or not isinstance(loc, dict):
        # Fallback to GPS stats from status
        status=sg(data,"dish_get_status") or data or {}
        gps=sg(status,"gps_stats") or {}

        sec("GPS STATUS")
        if gps:
            gps_valid=gps.get("gps_valid")
            gps_sats=gps.get("gps_sats")
            if gps_valid is not None:
                status_text=f"{C.BGRN}Locked{C.RST}" if gps_valid else f"{C.BRED}No Lock{C.RST}"
                pf("GPS Status",status_text)
            if gps_sats:
                pf("Satellites in View",gps_sats)
        else:
            print(f"  {C.BCYN}│{C.RST}  {C.DIM}(no location data){C.RST}")
        sec_end()
        return

    sec("LOCATION")
    lla=loc.get("lla") or {}
    lat=lla.get("lat")
    lon=lla.get("lon")
    alt=lla.get("alt")

    if lat is not None and lon is not None:
        pf("Latitude",f"{float(lat):.6f}°")
        pf("Longitude",f"{float(lon):.6f}°")
        if alt is not None:
            pf("Altitude",f"{float(alt):.1f} m")
        # Generate Google Maps link
        maps_url=f"https://maps.google.com/?q={float(lat)},{float(lon)}"
        pf("Google Maps Link",maps_url)

    source=loc.get("source")
    if source: pf("Source",source)

    sigma=loc.get("sigma_m")
    if sigma: pf("Accuracy (σ)",f"±{float(sigma):.1f} m")

    h_speed=loc.get("horizontal_speed_mps")
    if h_speed is not None: pf("Horizontal Speed",f"{float(h_speed):.2f} m/s")

    v_speed=loc.get("vertical_speed_mps")
    if v_speed is not None: pf("Vertical Speed",f"{float(v_speed):.2f} m/s")

    sec_end()

def _short_band(b):
    if not b: return ""
    return str(b).replace("RF_", "").replace("2GHZ", "2.4 GHz").replace("5GHZ_HIGH", "5 GHz (high)").replace("5GHZ", "5 GHz")


def _fmt_bytes(n):
    if n is None: return "N/A"
    try: v=float(n)
    except (TypeError, ValueError): return "N/A"
    for u in ("B","KB","MB","GB","TB"):
        if abs(v) < 1024: return f"{v:,.0f} {u}" if u in ("B","KB") else f"{v:,.1f} {u}"
        v /= 1024
    return f"{v:,.1f} PB"


def _time_ago_ns(ns):
    try: secs=float(ns)/1e9
    except (TypeError, ValueError): return ""
    if secs < 1577836800: return ""
    import time as _t
    diff=_t.time() - secs
    if diff < 2: return "now"
    if diff < 60: return f"{int(diff)}s ago"
    if diff < 3600: return f"{int(diff/60)}m ago"
    if diff < 86400: return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"


def _fmt_duration_ns(ns):
    try: s=float(ns)/1e9
    except (TypeError, ValueError): return "N/A"
    if s < 1: return f"{s*1000:.0f} ms"
    if s < 60: return f"{s:.1f} s"
    m=int(s/60); r=int(round(s - m*60))
    return f"{m}m {r}s" if r else f"{m}m"


def display_history(data):
    """Mirror the web History tab: power draw summary, outages, event log."""
    root=sg(data,"dish_get_history") or sg(data,"dishGetHistory") or sg(data,"getHistory") or data
    if not root:
        sec("HISTORY"); print(f"  {C.BCYN}│{C.RST}  {C.DIM}No history data.{C.RST}"); sec_end(); return

    power=sg(root,"power_in") or sg(root,"powerIn") or []
    sec("POWER DRAW")
    nums=[]
    for v in power:
        try: nums.append(float(v))
        except (TypeError, ValueError): pass
    if nums:
        last=nums[-1]
        lo=min(nums); hi=max(nums); avg=sum(nums)/len(nums)
        pf("Current",f"{last:.1f} W")
        pf("Min",f"{lo:.1f} W")
        pf("Max",f"{hi:.1f} W")
        pf("Avg",f"{avg:.1f} W")
        pf("Window",f"{len(nums)}s")
        # Simple sparkline using Unicode blocks
        bars=" ▁▂▃▄▅▆▇█"
        width=min(48, tw()-12)
        step=max(1, len(nums)//width) if len(nums) > width else 1
        samples=nums[::step][:width] if step > 1 else nums[-width:]
        rng=hi - lo or 1
        spark="".join(bars[max(0, min(len(bars)-1, int((n-lo)/rng * (len(bars)-1))))] for n in samples)
        print(f"  {C.BCYN}│{C.RST}     {C.CYN}{spark}{C.RST}")
    else:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No power samples.{C.RST}")
    sec_end()

    sec("OUTAGES")
    outages=sg(root,"outages") or []
    if isinstance(outages, list) and outages:
        pf("Recorded", str(len(outages)))
        sorted_out=sorted(outages, key=lambda o: float(sg(o,"start_timestamp_ns") or sg(o,"startTimestampNs") or 0), reverse=True)
        for o in sorted_out[:12]:
            cause=sg(o,"cause") or "UNKNOWN"
            dur=_fmt_duration_ns(sg(o,"duration_ns") or sg(o,"durationNs"))
            switch=sg(o,"did_switch") or sg(o,"didSwitch")
            tag="satellite switch" if switch else "no switch"
            print(f"  {C.BCYN}│{C.RST}    {C.BYEL}{cause:<24}{C.RST} {C.CYN}{dur:>10}{C.RST}  {C.DIM}{tag}{C.RST}")
    else:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No outages recorded.{C.RST}")
    sec_end()

    sec("EVENT LOG")
    event_log=sg(root,"event_log") or sg(root,"eventLog") or {}
    events=sg(event_log,"events") or []
    if isinstance(events, list) and events:
        pf("Recorded", str(len(events)))
        sorted_ev=sorted(events, key=lambda e: float(sg(e,"start_timestamp_ns") or sg(e,"startTimestampNs") or 0), reverse=True)
        for e in sorted_ev[:12]:
            sev=(sg(e,"severity") or "").replace("EVENT_SEVERITY_","") or "?"
            reason=(sg(e,"reason") or "").replace("EVENT_REASON_","").replace("_"," ").title()
            dur=_fmt_duration_ns(sg(e,"duration_ns") or sg(e,"durationNs"))
            ago=_time_ago_ns(sg(e,"start_timestamp_ns") or sg(e,"startTimestampNs"))
            print(f"  {C.BCYN}│{C.RST}    {C.BYEL}{sev:<10}{C.RST} {C.BWHT}{reason:<30}{C.RST} {C.CYN}{dur:>10}{C.RST}  {C.DIM}{ago}{C.RST}")
    else:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No events recorded.{C.RST}")
    sec_end()


def display_router_status(data):
    s=sg(data,"wifi_get_status") or sg(data,"get_status") or data or {}
    di=sg(s,"device_info") or {}
    uptime=sg(s,"device_state","uptime_s") or sg(s,"deviceState","uptimeS")
    alerts=sg(s,"alerts") or {}
    active=[k for k,v in alerts.items() if v] if isinstance(alerts,dict) else []

    sec("ROUTER STATUS")
    if active:
        pf("Health", f"{C.BYEL}{len(active)} alert(s){C.RST}")
    else:
        pf("Health", f"{C.BGRN}HEALTHY{C.RST}")
    pf("Uptime", fmt_up(uptime))
    for label, key in (("Ping to Dish","dish_ping_latency_ms"),
                       ("Ping to Point of Presence","pop_ping_latency_ms"),
                       ("Ping (WAN)","ping_latency_ms")):
        v=sg(s,key)
        if v is not None:
            try: pf(label, f"{float(v):.1f} ms")
            except (TypeError, ValueError): pf(label, v)
    sec_end()

    sec("ROUTER DEVICE")
    for label, key in (("Router ID","id"),
                       ("Hardware","hardware_version"),
                       ("Software","software_version"),
                       ("Country","country_code"),
                       ("Boot Count","bootcount"),
                       ("Dish Cohoused","dish_cohoused")):
        v=sg(di,key)
        if v is not None: pf(label, v)
    ipv4=sg(s,"ipv4_wan_address")
    if ipv4: pf("IPv4 WAN", ipv4)
    ipv6_list=sg(s,"ipv6_wan_addresses")
    if isinstance(ipv6_list, list) and ipv6_list: pf("IPv6 WAN", ipv6_list[0])
    leases=sg(s,"dhcp_servers") or []
    if isinstance(leases, list):
        total=sum(len(sg(srv,"leases") or []) for srv in leases)
        if total: pf("DHCP Leases", total)
    if active:
        pf("Active Alerts", ", ".join(active))
    sec_end()


def display_router_clients(data):
    resp=sg(data,"wifi_get_clients") or sg(data,"wifiGetClients") or data or {}
    clients=sg(resp,"clients") or []
    sec("WIFI CLIENTS")
    if not clients:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No clients connected.{C.RST}"); sec_end(); return
    pf("Connected", str(len(clients)))
    def rank(c):
        role=sg(c,"role") or ""
        sig=sg(c,"signal_strength")
        try: sig=float(sig) if sig is not None else -999
        except (TypeError, ValueError): sig=-999
        return (0 if role == "CONTROLLER" else 1, -sig)
    for c in sorted(clients, key=rank):
        name=sg(c,"name") or sg(c,"hostname") or sg(c,"given_name") or sg(c,"mac_address") or "unnamed"
        mac=sg(c,"mac_address") or ""
        ip=sg(c,"ip_address") or ""
        band=_short_band(sg(c,"iface"))
        rssi=sg(c,"signal_strength")
        role=sg(c,"role")
        tag=f"{C.BYEL}CTRL{C.RST} " if role == "CONTROLLER" else ""
        sub=" · ".join(x for x in (mac, ip) if x)
        meta=[]
        if rssi is not None:
            try: meta.append(f"{float(rssi):.0f} dBm")
            except (TypeError, ValueError): pass
        if band: meta.append(band)
        print(f"  {C.BCYN}│{C.RST}  {tag}{C.BWHT}{name}{C.RST}")
        if sub: print(f"  {C.BCYN}│{C.RST}    {C.DIM}{sub}{C.RST}")
        if meta: print(f"  {C.BCYN}│{C.RST}    {C.CYN}{'  '.join(meta)}{C.RST}")
    sec_end()


def display_router_networks(data):
    resp=sg(data,"wifi_get_config") or sg(data,"wifiGetConfig") or data or {}
    cfg=sg(resp,"wifi_config") or sg(resp,"wifiConfig") or resp
    networks=sg(cfg,"networks") or []
    sec("WIFI NETWORKS")
    if not networks:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No WiFi networks configured.{C.RST}"); sec_end(); return
    for net in networks:
        bss=sg(net,"basic_service_sets") or []
        ssid=sg(bss[0],"ssid") if bss else "(hidden)"
        is_guest=bool(sg(net,"guest"))
        isolated=bool(sg(net,"client_isolation"))
        tags=[]
        if is_guest: tags.append(f"{C.BYEL}GUEST{C.RST}")
        if isolated: tags.append(f"{C.BYEL}ISOLATED{C.RST}")
        tag_str=("  " + "  ".join(tags)) if tags else ""
        print(f"  {C.BCYN}│{C.RST}  {C.BWHT}{ssid}{C.RST}{tag_str}")
        bands=[_short_band(sg(b,"band")) for b in bss]
        bands=[b for b in bands if b]
        if bands: print(f"  {C.BCYN}│{C.RST}    {C.CYN}{'  '.join(bands)}{C.RST}")
        meta=[]
        ipv4=sg(net,"ipv4")
        if ipv4: meta.append(str(ipv4))
        domain=sg(net,"domain")
        if domain: meta.append(f"domain {domain}")
        vlan=sg(net,"vlan")
        if vlan: meta.append(f"VLAN {vlan}")
        lease=sg(net,"dhcpv4_lease_duration_s")
        if lease: meta.append(f"DHCP lease {lease}s")
        if meta: print(f"  {C.BCYN}│{C.RST}    {C.DIM}{' · '.join(meta)}{C.RST}")
        # Passwords are redacted per SENSITIVE_KEYS. This is intentional — the
        # router returns bullet characters over the LAN API anyway.
        print(f"  {C.BCYN}│{C.RST}    {C.DIM}password: [REDACTED]{C.RST}")
    sec_end()


def display_router_radios(data):
    resp=sg(data,"get_radio_stats") or data or {}
    radios=sg(resp,"radio_stats") or []
    sec("RADIO STATS")
    if not radios:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No radio data.{C.RST}"); sec_end(); return
    for r in radios:
        band=_short_band(sg(r,"band"))
        thermal=sg(r,"thermal_status") or {}
        temp=sg(thermal,"temp2")
        duty=sg(thermal,"duty_cycle")
        rx=sg(r,"rx_stats") or {}
        tx=sg(r,"tx_stats") or {}
        header=f"{C.BWHT}{band or '?'}{C.RST}"
        if temp is not None:
            try: header += f"  {C.CYN}{float(temp):.0f}°C{C.RST}"
            except (TypeError, ValueError): pass
        print(f"  {C.BCYN}│{C.RST}  {header}")
        stats=[
            ("RX", _fmt_bytes(sg(rx,"bytes"))),
            ("TX", _fmt_bytes(sg(tx,"bytes"))),
            ("RX pkts", sg(rx,"packets") or "—"),
            ("TX pkts", sg(tx,"packets") or "—"),
            ("Duty", f"{duty}%" if duty is not None else "—"),
        ]
        print(f"  {C.BCYN}│{C.RST}    " + "  ".join(f"{C.DIM}{k}{C.RST} {C.CYN}{v}{C.RST}" for k, v in stats))
    sec_end()


def display_router_selftest(data):
    resp=sg(data,"wifi_self_test") or data or {}
    st=sg(resp,"self_test") or {}
    rows=[]
    def push(name, obj):
        if not isinstance(obj, dict): return
        rows.append((name, bool(obj.get("success")), obj.get("failure_reason") or ""))
    for k, v in (st.items() if isinstance(st, dict) else []):
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    push(sg(item,"name") or k, item)
        elif isinstance(v, dict):
            push(sg(v,"name") or k, v)
    sec("SELF-TEST")
    if not rows:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No self-test results.{C.RST}"); sec_end(); return
    for name, passed, reason in rows:
        icon=f"{C.BGRN}✔{C.RST}" if passed else f"{C.BRED}✘{C.RST}"
        label=f"{C.BWHT}{name}{C.RST}"
        if passed:
            print(f"  {C.BCYN}│{C.RST}  {icon}  {label}  {C.BGRN}pass{C.RST}")
        else:
            print(f"  {C.BCYN}│{C.RST}  {icon}  {label}  {C.BRED}fail{C.RST}")
            if reason:
                print(f"  {C.BCYN}│{C.RST}      {C.DIM}{reason}{C.RST}")
    sec_end()


def display_router_interfaces(data):
    resp=sg(data,"get_network_interfaces") or data or {}
    ifaces=sg(resp,"network_interfaces") or []
    sec("NETWORK INTERFACES")
    if not ifaces:
        print(f"  {C.BCYN}│{C.RST}  {C.DIM}No interfaces reported.{C.RST}"); sec_end(); return
    up_count=sum(1 for i in ifaces if sg(i,"up"))
    pf("Up", f"{up_count}/{len(ifaces)}")
    for iface in ifaces:
        name=sg(iface,"name") or "?"
        up=bool(sg(iface,"up"))
        state=f"{C.BGRN}up{C.RST}" if up else f"{C.BRED}down{C.RST}"
        kind=""
        if sg(iface,"ethernet"): kind="ETH"
        elif sg(iface,"wifi"): kind="WIFI"
        print(f"  {C.BCYN}│{C.RST}  {C.BWHT}{name}{C.RST}  {C.DIM}{kind}{C.RST}  {state}")
        ipv4=sg(iface,"ipv4_addresses") or []
        ipv6=sg(iface,"ipv6_addresses") or []
        addrs=" · ".join(list(ipv4) + list(ipv6)[:1])
        if addrs: print(f"  {C.BCYN}│{C.RST}    {C.CYN}{addrs}{C.RST}")
        mac=sg(iface,"mac_address")
        meta=[]
        if mac: meta.append(f"mac {mac}")
        eth=sg(iface,"ethernet") or {}
        if eth:
            speed=sg(eth,"speed_mbps") or "?"
            duplex=sg(eth,"duplex") or ""
            meta.append(f"link {speed} Mbps {duplex}".strip())
        wifi=sg(iface,"wifi") or {}
        ch=sg(wifi,"channel")
        if ch: meta.append(f"ch {ch}")
        rxB=sg(sg(iface,"rx_stats") or {}, "bytes")
        txB=sg(sg(iface,"tx_stats") or {}, "bytes")
        if rxB or txB: meta.append(f"rx/tx {_fmt_bytes(rxB)} / {_fmt_bytes(txB)}")
        if meta: print(f"  {C.BCYN}│{C.RST}    {C.DIM}{'  '.join(meta)}{C.RST}")
    sec_end()


def display_obstruction_map(data):
    om=sg(data,"dishGetObstructionMap") or sg(data,"dish_get_obstruction_map") or data
    snr=sg(om,"snr") or []; nr=int(sg(om,"num_rows") or sg(om,"numRows") or 0)
    nc=int(sg(om,"num_cols") or sg(om,"numCols") or 0)
    if not snr or not nr: print(f"  {C.DIM}No obstruction map data.{C.RST}"); return
    sec("OBSTRUCTION MAP")
    pf("Grid",f"{nc}×{nr}")
    print(f"  {C.BCYN}│{C.RST}  {C.DIM}{C.BGRN}█{C.RST}=clear {C.YEL}▓{C.RST}=partial {C.BRED}░{C.RST}=obstructed {C.DIM}·{C.RST}=none")
    idx=0
    for r in range(nr):
        row=f"  {C.BCYN}│{C.RST}  "
        for col in range(nc):
            if idx<len(snr):
                v=float(snr[idx]) if snr[idx] else -1
                if v<0: row+=f"{C.DIM}·{C.RST}"
                elif v>2: row+=f"{C.BGRN}█{C.RST}"
                elif v>0.5: row+=f"{C.YEL}▓{C.RST}"
                else: row+=f"{C.BRED}░{C.RST}"
            idx+=1
        if r%2==0: print(row)
    sec_end()

def display_generic(data, title="RESPONSE"):
    if not data:
        sec(title); print(f"  {C.BCYN}│{C.RST}  {C.DIM}(empty response){C.RST}"); sec_end(); return
    if not isinstance(data, dict):
        sec(title); print(f"  {C.BCYN}│{C.RST}  {C.DIM}{str(data)}{C.RST}"); sec_end(); return
    def _pd(d,depth=0):
        if not isinstance(d, dict): return
        for k,v in d.items():
            if isinstance(v,dict): pf(k.replace("_"," ").title(),"",ind=depth+1); _pd(v,depth+1)
            elif isinstance(v,list):
                if len(v)<=5 and all(not isinstance(x,dict) for x in v):
                    pf(k.replace("_"," ").title(),str(v),ind=depth+1)
                elif v and isinstance(v[0],dict):
                    pf(k.replace("_"," ").title(),f"[{len(v)} items]",ind=depth+1)
                    for i,item in enumerate(v[:8]):
                        print(f"  {C.BCYN}│{C.RST}{'   '*(depth+2)}{C.DIM}#{i+1}:{C.RST}")
                        _pd(item,depth+2)
                else: pf(k.replace("_"," ").title(),f"[{len(v)} items]",ind=depth+1)
            else:
                pf(k.replace("_"," ").title(),v,ind=depth+1)
    sec(title); _pd(data); sec_end()

def scrub_sensitive_data(data):
    """Recursively redact sensitive fields (passwords, etc.) from response
    data before display. Matches starlink-web.py's _scrub semantics."""
    if data is None:
        return data
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else scrub_sensitive_data(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [scrub_sensitive_data(x) for x in data]
    return data

def display_raw(data, title="RAW JSON"):
    sec(title)
    if data is not None:
        data = scrub_sensitive_data(data)  # Scrub credentials before display
        f=json.dumps(data,indent=2,default=str); ls=f.split("\n")
        for l in ls: print(f"  {C.BCYN}│{C.RST}  {C.DIM}{l}{C.RST}")
    else: print(f"  {C.BCYN}│{C.RST}  {C.DIM}(no data){C.RST}")
    sec_end()

# ── Menu ──────────────────────────────────────────────────────

def mo(k,l,d="",ic=""):
    print(f"  {C.BCYN}{C.BOLD}  [{k:>2}]{C.RST}  {ic} {C.BWHT}{l}{C.RST}"+(f"  {C.DIM}{d}{C.RST}" if d else ""))

def main_menu(dish_conn, router_conn):
    clear(); banner()
    ds=f"{C.BGRN}● Dish{C.RST}" if dish_conn else f"{C.BRED}● Dish{C.RST}"
    rs=f"{C.BGRN}● Router{C.RST}" if router_conn else f"{C.BRED}● Router{C.RST}"
    print(f"  {C.BOLD}Status:{C.RST} {ds}  {rs}"); print()
    hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} DISH TELEMETRY{C.RST}"); hr("─",C.DIM)
    mo("1","Device Info","hardware, software, IDs","🛰")
    mo("2","Dish Status","state, signal, alignment, alerts","📡")
    mo("3","GPS & Location","lat/lon, altitude, maps link","🗺")
    mo("4","Obstruction Map","visual obstruction grid","🗺")
    mo("5","History","power draw, outages, events","📈")
    mo("6","Dish Config","configuration snapshot (read-only)","⚙")
    mo("7","Diagnostics","internal diagnostics","🩺")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} ROUTER{C.RST}"); hr("─",C.DIM)
    mo("8","Router Status","uptime, pings, device info","🛜")
    mo("9","WiFi Clients","connected devices","👥")
    mo("10","WiFi Networks","SSIDs, bands, VLANs","📶")
    mo("11","Radio Stats","temp, RX/TX, duty","📻")
    mo("12","Self-test","wifi self-test results","✅")
    mo("13","Network Interfaces","up/down, addrs, MACs","🔌")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} ACTIONS{C.RST}"); hr("─",C.DIM)
    mo("14","Reboot Dish","restart the dish","🔄")
    mo("15","Ping 192.168.100.1","quick connectivity test","⚡")
    mo("16","Export Full Dump","dish+router data to JSON","💾")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} ADVANCED{C.RST}"); hr("─",C.DIM)
    mo("17","gRPC Services","list services & methods","🔍")
    mo("18","Request Fields","all API request types","📋")
    mo("19","Raw gRPC Request","send custom JSON","💻")
    mo("20","Live Monitor","auto-refreshing status","📺")
    mo("21","Reconnect","re-establish dish+router","🔗")
    print(); hr("─",C.DIM); mo("q","Quit","","👋"); print()

# ── Actions ───────────────────────────────────────────────────

def act_reboot(d):
    if not confirm("Reboot the dish? Connectivity will drop."): return
    r=d.request("reboot"); ps("Reboot sent.",ok=True)
    if r: display_raw(r,"REBOOT")  # Note: 'reboot' has no underscore in the API

# ── Advanced ──────────────────────────────────────────────────

def ping_test():
    sec("PING → 192.168.100.1")
    try:
        # Use absolute path to prevent PATH hijacking
        ping_bin = shutil.which("ping") or "/bin/ping"
        r=subprocess.run([ping_bin,"-c","10","-i","0.3","192.168.100.1"],capture_output=True,text=True,timeout=15)
        for l in r.stdout.strip().split("\n"):
            # Strip ANSI escape sequences to prevent terminal injection
            l = l.replace("\033", "")
            print(f"  {C.BCYN}│{C.RST}  {l}")
    except Exception as e: print(f"  {C.BCYN}│{C.RST}  {C.BRED}{e}{C.RST}")
    sec_end()

def list_services(d):
    sec("gRPC SERVICES & METHODS")
    for sn,ms in d.list_all_services().items():
        print(f"  {C.BCYN}│{C.RST}  {C.BOLD}{sn}{C.RST}")
        for m in ms: print(f"  {C.BCYN}│{C.RST}    {C.CYN}→ {m}{C.RST}")
    sec_end()

def list_fields(d):
    sec("REQUEST FIELDS (Handle API)")
    fields=d.list_request_fields()
    if fields:
        for n,t in sorted(fields,key=lambda x:x[0]):
            ts=str(t).split(".")[-1] if "." in str(t) else str(t)
            print(f"  {C.BCYN}│{C.RST}  {C.BWHT}{n:40}{C.RST} {C.DIM}({ts}){C.RST}")
    else:
        print(f"  {C.BCYN}│{C.RST}  {C.BOLD}Common request keys:{C.RST}")
        for k in ["get_device_info","get_status","dish_get_obstruction_map",
                   "dish_get_config","get_location","get_diagnostics","get_history","reboot",
                   "transceiver_get_status","transceiver_get_telemetry"]:
            print(f"  {C.BCYN}│{C.RST}    {C.CYN}{k}{C.RST}")
    sec_end()

def export_all(dish, router):
    print(f"\n  {C.BYEL}⚠  Export contains sensitive data:{C.RST}")
    print(f"  {C.DIM}   • GPS coordinates (latitude, longitude, altitude){C.RST}")
    print(f"  {C.DIM}   • Device identifiers, router config, WiFi SSIDs{C.RST}")
    print(f"  {C.DIM}   • Passwords are redacted before writing.{C.RST}")
    if not confirm("Export all data to file?"): return

    print(f"\n  {C.DIM}Collecting all data …{C.RST}")
    all_data={"dish": {}, "router": {}}
    dish_keys=["get_device_info","get_status","dish_get_obstruction_map",
               "dish_get_config","get_location","get_diagnostics",
               "get_history","transceiver_get_status","transceiver_get_telemetry"]
    for k in dish_keys:
        print(f"  {C.DIM}  dish → {k}{C.RST}",end=" ",flush=True)
        r=dish.request(k) if dish._connected else None
        all_data["dish"][k]=r
        print(f"{C.BGRN}✔{C.RST}" if r else f"{C.YEL}–{C.RST}")
    router_keys=["get_status","get_device_info","get_diagnostics","get_history",
                 "wifi_get_clients","wifi_get_config","wifi_self_test",
                 "get_network_interfaces","get_radio_stats"]
    if router and router._connected:
        for k in router_keys:
            print(f"  {C.DIM}  router → {k}{C.RST}",end=" ",flush=True)
            r=router.request(k)
            all_data["router"][k]=r
            print(f"{C.BGRN}✔{C.RST}" if r else f"{C.YEL}–{C.RST}")
    else:
        print(f"  {C.DIM}  router → not connected, skipping{C.RST}")

    # Redact any PSK / credential fields before writing to disk.
    all_data=scrub_sensitive_data(all_data)

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    home=os.path.expanduser("~")
    if not os.path.isdir(home):
        ps("Invalid home directory.",ok=False); return
    fn=os.path.join(home, f"starlink_dump_{ts}.json")
    try:
        with open(fn,"w") as f: json.dump(all_data,f,indent=2,default=str)
        os.chmod(fn, 0o600)  # Restrict file to user only
    except Exception as e:
        ps(f"Failed to write file: {e}",ok=False); return
    print(); ps(f"Saved → {C.BOLD}{fn}{C.RST}  ({os.path.getsize(fn):,} bytes)",ok=True)

def live_monitor(d):
    print(f"\n  {C.BOLD}Live Monitor{C.RST} {C.DIM}(Ctrl+C to stop){C.RST}")
    try:
        hide_cursor()
        while True:
            clear()
            print(f"  {C.BCYN}{C.BOLD}✦ STARLINK MINI · LIVE{C.RST}  {C.DIM}{datetime.now().strftime('%H:%M:%S')}  Ctrl+C stop{C.RST}")
            hr("─",C.DIM)
            data=d.request("get_status")
            if data:
                s=sg_coalesce(data, ["dish_get_status","dishGetStatus"], default=data)
                disablement=sg_coalesce(s, ["disablement_code"], default="UNKNOWN")
                dl=sg_coalesce(s, ["downlink_throughput_bps","downlinkThroughputBps"], default=0)
                ul=sg_coalesce(s, ["uplink_throughput_bps","uplinkThroughputBps"], default=0)
                lat=sg_coalesce(s, ["pop_ping_latency_ms","popPingLatencyMs"], default=0)
                drop=sg_coalesce(s, ["pop_ping_drop_rate","popPingDropRate"], default=0)
                snr=sg_coalesce(s, ["snr_above_noise_floor","snrAboveNoiseFloor"])
                dev_state=sg_coalesce(s, ["device_state","deviceState"], default={})
                up=sg(dev_state, "uptime_s") or sg(dev_state, "uptimeS") or 0
                obs_stats=sg_coalesce(s, ["obstruction_stats","obstructionStats"], default={})
                obs=sg(obs_stats, "fraction_obstructed") or sg(obs_stats, "fractionObstructed") or 0
                sc=C.BGRN if disablement == "OKAY" else C.BYEL
                print(f"\n  {C.BOLD}Status:{C.RST} {sc}{disablement}{C.RST}   {C.BOLD}Uptime:{C.RST} {C.CYN}{fmt_up(up)}{C.RST}\n")
                print(f"  {C.BOLD}↓ DL:{C.RST}  {C.BGRN}{fmt_bps(dl):>14}{C.RST}   {C.BOLD}↑ UL:{C.RST}  {C.BBLU}{fmt_bps(ul):>14}{C.RST}")
                print(f"  {C.BOLD}Lat:{C.RST}   {C.CYN}{float(lat):>10.1f} ms{C.RST}    {C.BOLD}Drop:{C.RST}  {C.CYN}{fmt_pct(drop):>10}{C.RST}")
                print(f"  {C.BOLD}SNR:{C.RST}   {C.CYN}{fmt_snr(snr):>14}{C.RST}   {C.BOLD}Obstr:{C.RST} {C.CYN}{fmt_pct(obs):>10}{C.RST}")
                bw=min(40,tw()-20); mx=300_000_000
                db=int(min(float(dl)/mx,1)*bw) if dl else 0
                ub=int(min(float(ul)/mx,1)*bw) if ul else 0
                print(f"\n  {C.BOLD}DL{C.RST} {C.BGRN}{'█'*db}{'░'*(bw-db)}{C.RST}")
                print(f"  {C.BOLD}UL{C.RST} {C.BBLU}{'█'*ub}{'░'*(bw-ub)}{C.RST}")
                print(f"  {C.DIM}{'0':>3} {'':─>{bw-8}} {fmt_bps(mx):>8}{C.RST}")
                alerts=sg(s,"alerts") or {}
                active=[k for k,v in alerts.items() if v] if isinstance(alerts,dict) else []
                if active: print(f"\n  {C.BYEL}⚠  {', '.join(active)}{C.RST}")
            else: print(f"\n  {C.BRED}Cannot reach dish.{C.RST}")
            time.sleep(3)
    except KeyboardInterrupt:
        print(f"\n\n  {C.DIM}Stopped.{C.RST}")
    finally:
        show_cursor()

# ── Main ──────────────────────────────────────────────────────

def main():
    enter_alt()
    try:
        dish=DishClient(DISH_ADDR)
        router=DishClient(ROUTER_ADDR)
        clear(); banner()
        print(f"  {C.DIM}Connecting to dish at {DISH_ADDR} …{C.RST}")
        ok,msg=dish.connect()
        if ok: ps(f"Dish connected — {len(dish._services)} service(s) discovered",ok=True)
        else: ps(f"Dish failed: {msg}",ok=False)
        print(f"  {C.DIM}Connecting to router at {ROUTER_ADDR} …{C.RST}")
        rok,rmsg=router.connect()
        if rok: ps(f"Router connected — {len(router._services)} service(s) discovered",ok=True)
        else: ps(f"Router failed: {rmsg}",ok=False)
        if not ok or not rok:
            print(f"\n  {C.YEL}Menu still works. Use [21] to reconnect later.{C.RST}")
        pause()

        while True:
            main_menu(dish._connected, router._connected)
            ch=input(f"  {C.BCYN}▸{C.RST} Select: ").strip().lower()
            if ch=="q":
                dish.close(); router.close(); break

            # Options that run outside capture_lines (need their own TTY loops)
            if ch=="20":
                live_monitor(dish)
                show_cursor()
                continue

            if ch=="19":
                try:
                    raw=input("\n  JSON> ").strip()
                except KeyboardInterrupt:
                    print(f"\n  {C.DIM}Cancelled.{C.RST}")
                    continue
                if raw:
                    try:
                        p=json.loads(raw)
                        keys=list(p.keys())
                        if len(keys)>1:
                            print(f"  {C.BRED}Warning: only first key will be sent ({keys[0]}); others ignored.{C.RST}")
                        r=dish.request(keys[0],p.get(keys[0],{}))
                        with capture_lines() as buf:
                            display_raw(r,"RAW RESPONSE")
                        render_screen(buf.getvalue().splitlines())
                    except json.JSONDecodeError as e:
                        print(f"  {C.BRED}Bad JSON: {e}{C.RST}")
                continue

            if ch in ("14","16"):
                # Actions that prompt for confirmation or print progress inline
                if ch=="14": act_reboot(dish)
                elif ch=="16": export_all(dish, router)
                pause()
                continue

            if ch=="21":
                print(f"  {C.DIM}Reconnecting …{C.RST}")
                dish.close(); router.close()
                dish=DishClient(DISH_ADDR); dok,dmsg=dish.connect()
                router=DishClient(ROUTER_ADDR); rok,rmsg=router.connect()
                ps(f"Dish: {'OK' if dok else dmsg}", ok=dok)
                ps(f"Router: {'OK' if rok else rmsg}", ok=rok)
                pause()
                continue

            with capture_lines() as buf:
                if   ch=="1":
                    r=dish.request("getDeviceInfo") or dish.request("getStatus")
                    display_device_info(r or {})
                elif ch=="2":
                    status=dish.request("getStatus") or {}
                    diag=dish.request("getDiagnostics") or {}
                    display_status(status, diag=diag)
                elif ch=="3":
                    display_location(dish.request("getLocation") or {})
                elif ch=="4":
                    display_obstruction_map(dish.request("dishGetObstructionMap") or {})
                elif ch=="5":
                    display_history(dish.request("getHistory") or {})
                elif ch=="6":
                    display_generic(dish.request("dishGetConfig") or {}, "DISH CONFIG")
                elif ch=="7":
                    display_generic(dish.request("getDiagnostics") or {}, "DIAGNOSTICS")
                elif ch=="8":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_status(router.request("get_status") or {})
                elif ch=="9":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_clients(router.request("wifi_get_clients") or {})
                elif ch=="10":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_networks(router.request("wifi_get_config") or {})
                elif ch=="11":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_radios(router.request("get_radio_stats") or {})
                elif ch=="12":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_selftest(router.request("wifi_self_test") or {})
                elif ch=="13":
                    if not router._connected:
                        print(f"  {C.BRED}Router not connected. Use [21] to reconnect.{C.RST}")
                    else:
                        display_router_interfaces(router.request("get_network_interfaces") or {})
                elif ch=="15": ping_test()
                elif ch=="17": list_services(dish)
                elif ch=="18": list_fields(dish)
                else: print(f"  {C.BRED}Unknown: {ch}{C.RST}")
            render_screen(buf.getvalue().splitlines())

    finally:
        exit_alt()

if __name__=="__main__": main()
