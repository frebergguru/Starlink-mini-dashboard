#!/usr/bin/env python3
"""
STARLINK MINI · CONTROL CENTER
Full dish telemetry, diagnostics & config for GNU/Linux

Install:
  chmod +x starlink-mini.py
  ./starlink-mini.py

The script will automatically set up a virtual environment and install dependencies.
"""

import json, subprocess, sys, os, shutil, time, venv, getpass, math, io, contextlib
from datetime import datetime

def setup_venv():
    """Create venv and install dependencies if not already in one."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(script_dir, "venv")

    # Check if we're already in a virtual environment
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

    if in_venv:
        return  # Already in venv, proceed normally

    # Create venv if it doesn't exist
    if not os.path.exists(venv_dir):
        print(f"Creating virtual environment in {venv_dir}...")
        venv.create(venv_dir, with_pip=True)

    # Determine pip executable
    pip_exe = os.path.join(venv_dir, "bin", "pip")
    if not os.path.exists(pip_exe):
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")  # Windows fallback

    # Install dependencies
    packages = ["grpcio", "grpcio-reflection", "protobuf"]
    print("Installing dependencies...")
    subprocess.check_call([pip_exe, "install", "-q"] + packages)

    # Re-execute script in venv
    python_exe = os.path.join(venv_dir, "bin", "python")
    if not os.path.exists(python_exe):
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")  # Windows fallback

    print("Launching in virtual environment...\n")
    os.execv(python_exe, [python_exe, __file__] + sys.argv[1:])

setup_venv()

def check_deps():
    missing = []
    try: import grpc
    except ImportError: missing.append("grpcio")
    try: from google.protobuf import descriptor_pool
    except ImportError: missing.append("protobuf")
    try: from grpc_reflection.v1alpha import reflection_pb2
    except ImportError: missing.append("grpcio-reflection")
    if missing:
        print(f"\n  \033[91mMissing: {', '.join(missing)}\033[0m")
        print(f"  \033[1mInstall:\033[0m pip install grpcio grpcio-reflection protobuf --break-system-packages\n")
        sys.exit(1)

check_deps()

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool as dp, json_format, message_factory
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

DISH_ADDR = "192.168.100.1:9200"

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
        if len(pages) == 1:
            print(f"  {C.DIM}{status or 'Press Enter to return …'}{C.RST}", end="", flush=True)
            input()
        else:
            raw = input(f"  {C.DIM}Page {i+1}/{len(pages)} — Enter: next  q: menu{C.RST}  ").strip().lower()
            hide_cursor()
            if raw == "q":
                break
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
    v=float(v); return f"{v*100:.1f}%" if v<=1 else f"{v:.1f}%"

def fmt_snr(v):
    return f"{float(v):.1f} dB" if v is not None else "N/A"

def fmt_deg(v):
    return f"{float(v):.2f}°" if v is not None else "N/A"


class DishClient:
    def __init__(self, addr=DISH_ADDR):
        self.addr=addr; self.channel=None; self._pool=dp.DescriptorPool()
        self._connected=False; self._services=[]; self._loaded_files=set()

    def connect(self):
        try:
            self.channel=grpc.insecure_channel(self.addr, options=[
                ("grpc.max_receive_message_length",50*1024*1024),
                ("grpc.connect_timeout_ms",8000)])
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

    def _load_file_by_symbol(self, sym):
        if sym in self._loaded_files: return
        req=reflection_pb2.ServerReflectionRequest(file_containing_symbol=sym)
        loaded_any=False
        try:
            for r in self._rstub.ServerReflectionInfo(iter([req])):
                if r.HasField("file_descriptor_response"):
                    for fb in r.file_descriptor_response.file_descriptor_proto:
                        fd=descriptor_pb2.FileDescriptorProto(); fd.ParseFromString(fb)
                        for dep in fd.dependency: self._load_file_by_name(dep)
                        try:
                            self._pool.Add(fd)
                            loaded_any=True
                        except Exception as e:
                            pass
            if loaded_any: self._loaded_files.add(sym)
        except Exception as e:
            pass

    def _load_file_by_name(self, fn):
        if fn in self._loaded_files: return
        req=reflection_pb2.ServerReflectionRequest(file_by_filename=fn)
        loaded_any=False
        try:
            for r in self._rstub.ServerReflectionInfo(iter([req])):
                if r.HasField("file_descriptor_response"):
                    for fb in r.file_descriptor_response.file_descriptor_proto:
                        fd=descriptor_pb2.FileDescriptorProto(); fd.ParseFromString(fb)
                        for dep in fd.dependency: self._load_file_by_name(dep)
                        try:
                            self._pool.Add(fd)
                            loaded_any=True
                        except Exception as e:
                            pass
            if loaded_any: self._loaded_files.add(fn)
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
        if not found_service:
            pass
        if found_service and not found_method:
            pass
        return "/SpaceX.API.Device.Device/Handle","SpaceX.API.Device.Request","SpaceX.API.Device.Response"

    def request(self, key, body=None):
        if not self._connected:
            print(f"  {C.BRED}Not connected.{C.RST}"); return None
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
            details = e.details() or "(no details)"
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

def display_status(data):
    s=sg(data,"getStatus") or sg(data,"dish_get_status") or sg(data,"dishGetStatus") or data

    sec("DEVICE INFO")
    di=sg_coalesce(s, ["device_info","deviceInfo"], default={})
    if di:
        for k in ["id","hardware_version","hardwareVersion","software_version","softwareVersion",
                  "country_code","countryCode","bootcount","generation_number","build_id"]:
            v=sg(di,k)
            if v is not None: pf(k.replace("_"," ").title(),v)
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
                # Filter out NaN strings from API
                display_val = "N/A" if isinstance(v, str) and v.lower() == "nan" else v
                pf(k.replace("_"," ").title(),display_val)
        sec_end()

def display_history(data):
    h=sg(data,"getHistory") or sg(data,"dish_get_history") or sg(data,"dishGetHistory") or data
    if not h: print(f"  {C.DIM}No history data.{C.RST}"); return
    sec("HISTORY STATUS")
    current=sg(h,"current")
    if current: pf("Current Index",current)
    sec_end()

    sec("NETWORK HISTORY")
    pf("Current Index",sg(h,"current"))
    dl=sg(h,"downlink_throughput_bps") or sg(h,"downlinkThroughputBps") or []
    ul=sg(h,"uplink_throughput_bps") or sg(h,"uplinkThroughputBps") or []
    lat=sg(h,"pop_ping_latency_ms") or sg(h,"popPingLatencyMs") or []
    def avg(a,n=10):
        r=[float(x) for x in a[-n:] if x]; return sum(r)/len(r) if r else 0
    if dl:
        pf("Avg DL (10s)",fmt_bps(avg(dl)))
        peak=[float(x) for x in dl[-10:] if x]
        if peak: pf("Peak DL (10s)",fmt_bps(max(peak)))
    if ul: pf("Avg UL (10s)",fmt_bps(avg(ul)))
    if lat: pf("Avg Latency (10s)",f"{avg(lat):.1f} ms")
    pf("Total Samples",len(dl))
    outages=sg(h,"outages") or []
    if outages:
        print(f"  {C.BCYN}│{C.RST}"); print(f"  {C.BCYN}│{C.RST}  {C.BOLD}Recent outages:{C.RST}")
        for i,o in enumerate(outages[-8:]):
            cause=sg(o,"cause",default="?"); dur=sg(o,"duration_ns") or sg(o,"durationNs") or 0
            print(f"  {C.BCYN}│{C.RST}    {C.DIM}#{i+1}{C.RST}  {C.YEL}{cause}{C.RST}  {C.CYN}{float(dur)/1e9:.1f}s{C.RST}")
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

    if lat and lon:
        pf("Latitude",f"{float(lat):.6f}°")
        pf("Longitude",f"{float(lon):.6f}°")
        if alt:
            pf("Altitude",f"{float(alt):.1f} m")
        # Generate Google Maps link
        maps_url=f"https://maps.google.com/?q={float(lat)},{float(lon)}"
        pf("Google Maps Link",maps_url)

    source=loc.get("source")
    if source: pf("Source",source)

    sigma=loc.get("sigma_m")
    if sigma: pf("Accuracy (σ)",f"±{float(sigma):.1f} m")

    h_speed=loc.get("horizontal_speed_mps")
    if h_speed: pf("Horizontal Speed",f"{float(h_speed):.2f} m/s")

    v_speed=loc.get("vertical_speed_mps")
    if v_speed: pf("Vertical Speed",f"{float(v_speed):.2f} m/s")

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
                display_val = "N/A" if isinstance(v, str) and v.lower() == "nan" else v
                pf(k.replace("_"," ").title(),display_val,ind=depth+1)
    sec(title); _pd(data); sec_end()

def display_raw(data, title="RAW JSON"):
    sec(title)
    if data is not None:
        f=json.dumps(data,indent=2,default=str); ls=f.split("\n")
        for l in ls: print(f"  {C.BCYN}│{C.RST}  {C.DIM}{l}{C.RST}")
    else: print(f"  {C.BCYN}│{C.RST}  {C.DIM}(no data){C.RST}")
    sec_end()

# ── Menu ──────────────────────────────────────────────────────

def mo(k,l,d="",ic=""):
    print(f"  {C.BCYN}{C.BOLD}  [{k:>2}]{C.RST}  {ic} {C.BWHT}{l}{C.RST}"+(f"  {C.DIM}{d}{C.RST}" if d else ""))

def main_menu(conn):
    clear(); banner()
    st=f"{C.BGRN}● Connected{C.RST}" if conn else f"{C.BRED}● Disconnected{C.RST}"
    print(f"  {C.BOLD}Status:{C.RST} {st}"); print()
    hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} INFORMATION & TELEMETRY{C.RST}"); hr("─",C.DIM)
    mo("1","Device Info","hardware, software, IDs","🛰")
    mo("2","Dish Status","state, signal, GPS, temps","📡")
    mo("3","Obstruction Map","visual obstruction grid","🗺")
    mo("4","Location","GPS lat/lon, altitude, speed","🗺")
    mo("5","Diagnostics","internal diagnostics","🩺")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} CONFIGURATION{C.RST}"); hr("─",C.DIM)
    mo("6","View Dish Config","snow melt, power save","⚙")
    mo("7","Set Snow Melt Mode","off / auto","❄")
    mo("8","Set Power Save","start time & duration","🔋")
    mo("9","Set Dish Level Mode","tilt like bracket / force level","📐")
    mo("10","Set WiFi Network","SSID & password","🌐")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} ACTIONS{C.RST}"); hr("─",C.DIM)
    mo("11","Reboot Dish","restart the dish","🔄")
    mo("12","Reboot WiFi","restart the WiFi module","📡")
    print(); hr("═",C.BCYN); print(f"  {C.BOLD}{C.BWHT} ADVANCED{C.RST}"); hr("─",C.DIM)
    mo("13","Ping Dish","quick connectivity test","⚡")
    mo("14","List gRPC Methods","all services & methods","🔍")
    mo("15","List Request Fields","all API request types","📋")
    mo("16","Raw gRPC Request","send custom JSON","💻")
    mo("17","Export All Data","full dump to JSON","💾")
    mo("18","Continuous Monitor","live status dashboard","📺")
    mo("19","Reconnect","re-establish gRPC channel","🔗")
    print(); hr("─",C.DIM); mo("q","Quit","","👋"); print()

# ── Config setters ────────────────────────────────────────────

def config_snow_melt(d):
    print(f"\n  {C.BOLD}Snow Melt Mode:{C.RST}")
    print(f"    {C.CYN}1{C.RST} = OFF\n    {C.CYN}2{C.RST} = AUTO")
    ch=input(f"\n  Select [1-2]: ").strip()
    modes={"1":"SNOW_MELT_MODE_OFF","2":"SNOW_MELT_MODE_AUTO"}
    m=modes.get(ch)
    if not m: ps("Invalid.",ok=False); return
    if not confirm(f"Set snow melt → {m}?"): return
    r=d.request("dish_set_config",{"dish_config":{"snow_melt_mode":m}})
    display_raw(r,"SET SNOW MELT RESULT")

def config_power_save(d):
    print(f"\n  {C.BOLD}Power Save Schedule{C.RST} {C.DIM}(minutes from midnight UTC){C.RST}")
    try:
        start=int(input("  Start minute (0-1439): ").strip())
        dur=int(input("  Duration (0-1440): ").strip())
    except ValueError: ps("Invalid number.",ok=False); return
    if not (0 <= start <= 1439): ps("Start minute must be 0-1439.",ok=False); return
    if not (0 <= dur <= 1440): ps("Duration must be 0-1440.",ok=False); return
    if not confirm(f"Set power save: start={start}, duration={dur}?"): return
    r=d.request("dish_set_config",{"dish_config":{
        "power_save_start_minutes":start,"power_save_duration_minutes":dur,
        "power_save_mode":"POWER_SAVE_MODE_ENABLED" if dur>0 else "POWER_SAVE_MODE_DISABLED"}})
    display_raw(r,"SET POWER SAVE RESULT")

def config_level(d):
    print(f"\n  {C.BOLD}Level Dish Mode:{C.RST}")
    print(f"    {C.CYN}1{C.RST} = TILT LIKE BRACKET\n    {C.CYN}2{C.RST} = FORCE LEVEL")
    ch=input("\n  Select [1-2]: ").strip()
    modes={"1":"LEVEL_DISH_MODE_TILT_LIKE_BRACKET","2":"LEVEL_DISH_MODE_FORCE_LEVEL"}
    m=modes.get(ch)
    if not m: ps("Invalid.",ok=False); return
    if not confirm(f"Set → {m}?"): return
    r=d.request("dish_set_config",{"dish_config":{"level_dish_mode":m}})
    display_raw(r,"SET LEVEL DISH RESULT")

def config_wifi(d):
    print(f"\n  {C.BOLD}Set WiFi Network{C.RST}")
    ssid=input("  SSID: ").strip()
    pw=getpass.getpass("  Password: ").strip()
    if not ssid: ps("SSID empty.",ok=False); return
    if len(pw)<8: ps("Password must be ≥8 chars.",ok=False); return
    print(f"\n  {C.BOLD}Band:{C.RST}\n    {C.CYN}1{C.RST}=2.4GHz  {C.CYN}2{C.RST}=5GHz  {C.CYN}3{C.RST}=Both")
    bc=input("  Select [1-3, default=3]: ").strip() or "3"
    if not confirm(f"Update WiFi → SSID='{ssid}'?"): return
    net={"network_name":ssid,"auth":"WPA2","password":pw}
    if bc=="1": net["band"]="WIFI_BAND_2_4GHZ"
    elif bc=="2": net["band"]="WIFI_BAND_5GHZ"
    r=d.request("wifi_set_config",{"wifi_config":{"networks":[net]}})
    display_raw(r,"SET WIFI RESULT")

# ── Actions ───────────────────────────────────────────────────

def act_stow(d):
    if not confirm("Stow the dish?"): return
    r=d.request("dish_stow"); ps("Stow sent.",ok=True)
    if r: display_raw(r,"STOW")

def act_unstow(d):
    ps("Unstow is not available via API on this Starlink model.",ok=False)
    print(f"  {C.DIM}The dish must be manually unstowed or automatically recovered.{C.RST}")

def act_reboot(d):
    if not confirm("Reboot the dish? Connectivity will drop."): return
    r=d.request("reboot"); ps("Reboot sent.",ok=True)
    if r: display_raw(r,"REBOOT")  # Note: 'reboot' has no underscore in the API

def act_reboot_wifi(d):
    ps("WiFi reboot is not available via API on this Starlink model.",ok=False)
    print(f"  {C.DIM}Reboot the entire system using option [22] instead.{C.RST}")

# ── Advanced ──────────────────────────────────────────────────

def ping_test():
    sec("PING → 192.168.100.1")
    try:
        r=subprocess.run(["ping","-c","10","-i","0.3","192.168.100.1"],capture_output=True,text=True,timeout=15)
        for l in r.stdout.strip().split("\n"): print(f"  {C.BCYN}│{C.RST}  {l}")
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
                   "dish_get_config","get_location","get_diagnostics","reboot","dish_stow",
                   "transceiver_get_status","transceiver_get_telemetry"]:
            print(f"  {C.BCYN}│{C.RST}    {C.CYN}{k}{C.RST}")
    sec_end()

def raw_req(d):
    sec("RAW gRPC REQUEST")
    print(f"  {C.BCYN}│{C.RST}  Enter JSON, e.g.  {{\"get_device_info\":{{}}}}"); sec_end()
    try:
        raw=input("\n  JSON> ").strip()
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Cancelled.{C.RST}")
        return
    if not raw: return
    try: p=json.loads(raw)
    except json.JSONDecodeError as e: ps(f"Bad JSON: {e}",ok=False); return
    keys=list(p.keys())
    if len(keys)>1: ps(f"Warning: only first key will be sent ({keys[0]}); others ignored.",ok=False)
    r=d.request(keys[0],p.get(keys[0],{}))
    display_raw(r,"RAW RESPONSE")

def export_all(d):
    print(f"\n  {C.DIM}Collecting all data …{C.RST}")
    all_data={}
    for k in ["get_device_info","get_status","dish_get_obstruction_map",
              "dish_get_config","get_location","get_diagnostics","reboot","dish_stow",
              "transceiver_get_status","transceiver_get_telemetry"]:
        print(f"  {C.DIM}  → {k}{C.RST}",end=" ",flush=True)
        r=d.request(k); all_data[k]=r
        print(f"{C.BGRN}✔{C.RST}" if r else f"{C.YEL}–{C.RST}")
    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    fn=os.path.expanduser(f"~/starlink_dump_{ts}.json")
    with open(fn,"w") as f: json.dump(all_data,f,indent=2,default=str)
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
                up=sg(sg_coalesce(s, ["device_state","deviceState"], default={}), "uptime_s") or sg(sg_coalesce(s, ["device_state","deviceState"], default={}), "uptimeS") or 0
                obs=sg(sg_coalesce(s, ["obstruction_stats","obstructionStats"], default={}), "fraction_obstructed") or sg(sg_coalesce(s, ["obstruction_stats","obstructionStats"], default={}), "fractionObstructed") or 0
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
        clear(); banner()
        print(f"  {C.DIM}Connecting to {DISH_ADDR} …{C.RST}")
        ok,msg=dish.connect()
        if ok: ps(f"Connected — {len(dish._services)} service(s) discovered",ok=True)
        else:
            ps(f"Failed: {msg}",ok=False)
            print(f"\n  {C.YEL}Menu still works. Use [19] to reconnect later.{C.RST}")
        pause()

        while True:
            main_menu(dish._connected)
            ch=input(f"  {C.BCYN}▸{C.RST} Select: ").strip().lower()
            if ch=="q":
                dish.close(); break

            # Option 18 needs to be outside capture_lines() (it has its own loop)
            if ch=="18":
                live_monitor(dish)
                show_cursor()  # ensure cursor is shown after live_monitor
                continue

            # Option 16 needs special handling for input(), but output shown via render_screen()
            if ch=="16":
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

            with capture_lines() as buf:
                if   ch=="1":  r=dish.request("getDeviceInfo") or dish.request("getStatus"); display_device_info(r or {})
                elif ch=="2":  display_status(dish.request("getStatus") or {})
                elif ch=="3":  display_obstruction_map(dish.request("dishGetObstructionMap") or {})
                elif ch=="4":
                    r=dish.request("getLocation") or {}
                    display_location(r)
                elif ch=="5":  display_generic(dish.request("getDiagnostics") or {},"DIAGNOSTICS")
                elif ch=="6":  display_generic(dish.request("dishGetConfig") or {},"DISH CONFIG")
                elif ch=="7":  config_snow_melt(dish)
                elif ch=="8":  config_power_save(dish)
                elif ch=="9":  config_level(dish)
                elif ch=="10": config_wifi(dish)
                elif ch=="11": act_reboot(dish)
                elif ch=="12": act_reboot_wifi(dish)
                elif ch=="13": ping_test()
                elif ch=="14": list_services(dish)
                elif ch=="15": list_fields(dish)
                elif ch=="17": export_all(dish)
                elif ch=="19":
                    print(f"  {C.DIM}Reconnecting …{C.RST}"); dish.close()
                    dish=DishClient(DISH_ADDR); ok,msg=dish.connect()
                    ps("Reconnected." if ok else f"Failed: {msg}",ok=ok)
                else: print(f"  {C.BRED}Unknown: {ch}{C.RST}")
            render_screen(buf.getvalue().splitlines())

    finally:
        exit_alt()

if __name__=="__main__": main()
