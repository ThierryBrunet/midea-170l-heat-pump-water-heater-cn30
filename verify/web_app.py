#!/usr/bin/env python3
"""Read-only CN30 panel: EW-11 TCP or USB-UART + MAX485."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import extract_frames, parse_frame  # noqa: E402
from verify.ew11 import wifi_status  # noqa: E402
from verify.serial_link import DEFAULT_SERIAL, open_link  # noqa: E402

STATIC = ROOT / "static"
_lock = threading.Lock()
_cfg: dict = {
    "serial": None,
    "baud": 600,
    "host": "192.168.31.219",
    "port": 502,
}
_cn30: dict = {"frames": 0, "last": None, "error": None, "bytes": 0, "age_s": None}
_wifi: dict = {
    "ssid": None,
    "rssi": None,
    "state": None,
    "bssid": None,
    "error": None,
    "ts": None,
}


def _link_label() -> str:
    if _cfg.get("serial"):
        return f"{_cfg['serial']} @ {_cfg['baud']} 8N1"
    return f"{_cfg['host']}:{_cfg['port']}"


def _cn30_loop() -> None:
    buf = b""
    while True:
        try:
            link = open_link(
                serial=_cfg.get("serial"),
                baud=_cfg["baud"],
                host=_cfg.get("host"),
                port=_cfg["port"],
            )
            with _lock:
                _cn30["error"] = None
            while True:
                chunk = link.read(512)
                if not chunk:
                    time.sleep(0.05)
                    continue
                buf += chunk
                frames, buf = extract_frames(buf)
                if len(buf) > 4096:
                    buf = buf[-256:]
                if not frames:
                    continue
                last = parse_frame(frames[-1])
                with _lock:
                    _cn30["frames"] += len(frames)
                    _cn30["bytes"] += sum(len(f) for f in frames)
                    _cn30["last"] = last.to_dict()
                    _cn30["last_ts"] = time.time()
                    _cn30["error"] = None
        except Exception as exc:
            with _lock:
                _cn30["error"] = str(exc)
            time.sleep(2)
        else:
            try:
                link.close()
            except Exception:
                pass


def _wifi_loop() -> None:
    """EW-11 STA quality. Quality is 0-100, not dBm. No credentials stored."""
    while True:
        host = _cfg.get("host")
        if not host:
            time.sleep(15)
            continue
        st = wifi_status(host, timeout=6)
        with _lock:
            _wifi.update(st)
            _wifi["ts"] = time.time()
        time.sleep(20)


def _snapshot() -> dict:
    with _lock:
        cn = dict(_cn30)
        wifi = {k: v for k, v in _wifi.items() if k != "ts"}
        wifi_ts = _wifi.get("ts")
    last = cn.get("last")
    ts = cn.get("last_ts")
    if ts:
        cn["age_s"] = round(time.time() - ts, 1)
    if wifi_ts:
        wifi["age_s"] = round(time.time() - wifi_ts, 1)
    payload = {
        "protocol": "cn30",
        "link": _link_label(),
        "serial": _cfg.get("serial"),
        "baud": _cfg["baud"],
        "host": _cfg.get("host"),
        "port": _cfg["port"],
        "connected": bool(last) and not cn.get("error"),
        "wifi": wifi,
        "cn30": cn,
        "power": None,
        "mode": None,
        "timer": None,
        "operation": None,
        "target_c": None,
        "mode_max_c": None,
        "mode_min_c": None,
        "eev": None,
        "checksum_ok": None,
        "temps": {},
        "errors": [],
        "readonly": True,
    }
    if last:
        payload["power"] = last.get("power")
        payload["mode"] = last.get("mode")
        payload["timer"] = last.get("timer")
        payload["operation"] = last.get("mode") if last.get("power") else "off"
        payload["target_c"] = last.get("target_c")
        payload["temps"] = last.get("temps") or {}
        payload["mode_max_c"] = last.get("mode_max_c")
        payload["mode_min_c"] = last.get("mode_min_c")
        payload["eev"] = last.get("eev")
        payload["checksum_ok"] = last.get("checksum_ok")
        payload["connected"] = cn.get("age_s") is not None and cn["age_s"] <= 15
    if cn.get("error"):
        payload["errors"].append("cn30: " + str(cn["error"]))
        payload["connected"] = False
    elif cn.get("age_s") is not None and cn["age_s"] > 15:
        payload["errors"].append(f"cn30: last frame {cn['age_s']}s ago")
    if wifi.get("error"):
        payload["errors"].append("wifi: " + str(wifi["error"]))
    return payload


def _selftest_from_cache() -> dict:
    """Do not open a second serial port; /dev/ttyUSB0 is exclusive."""
    snap = _snapshot()
    last = (snap.get("cn30") or {}).get("last")
    steps = []
    err = (snap.get("cn30") or {}).get("error")
    steps.append({"name": "link", "ok": not err, "detail": snap["link"] + (f" {err}" if err else " ok"), "hint": ""})
    if not last:
        steps.append(
            {
                "name": "cn30_frame",
                "ok": False,
                "detail": "no frame yet",
                "hint": "Wait ~4s or swap A+/B-.",
            }
        )
        return {"steps": steps, "passed": False}
    steps.append({"name": "cn30_frame", "ok": True, "detail": last.get("hex", "")[:48], "hint": ""})
    steps.append({"name": "checksum", "ok": bool(last.get("checksum_ok")), "detail": "ok" if last.get("checksum_ok") else "fail", "hint": ""})
    steps.append(
        {
            "name": "mode",
            "ok": last.get("mode") is not None,
            "detail": f"{last.get('mode')} timer={last.get('timer')} power={last.get('power')}",
            "hint": "",
        }
    )
    tgt = last.get("target_c")
    steps.append(
        {
            "name": "setpoint",
            "ok": tgt is not None and 55 <= tgt <= 75,
            "detail": f"{tgt} °C",
            "hint": "",
        }
    )
    temps = last.get("temps") or {}
    for name in ("t5c", "t3", "t4", "tp"):
        v = temps.get(name)
        ok = v is not None and -20 <= v <= 95
        steps.append({"name": f"temp_{name}", "ok": ok, "detail": f"{v} °C" if v is not None else "missing", "hint": ""})
    return {"steps": steps, "passed": all(s["ok"] for s in steps)}


def _json_body(handler: BaseHTTPRequestHandler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    raw = handler.rfile.read(n)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "Midea170Cn30/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            try:
                self._json(200, _snapshot())
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            _json_body(self)
        except json.JSONDecodeError as exc:
            self._json(400, {"error": f"bad json: {exc}"})
            return
        if path == "/api/selftest":
            self._json(200, _selftest_from_cache())
            return
        self._json(400, {"error": "read-only: CN30 writes are not enabled"})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Midea 170L CN30 panel (read-only)")
    p.add_argument("--serial", default="", help="e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=600)
    p.add_argument("--host", default="192.168.31.219")
    p.add_argument("--port", type=int, default=502)
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--http-port", type=int, default=8765)
    ns = p.parse_args(argv)
    _cfg.update(
        serial=ns.serial or None,
        baud=ns.baud,
        host=ns.host,
        port=ns.port,
    )
    threading.Thread(target=_cn30_loop, name="cn30", daemon=True).start()
    threading.Thread(target=_wifi_loop, name="ew11-wifi", daemon=True).start()
    httpd = ThreadingHTTPServer((ns.listen, ns.http_port), Handler)
    print(f"Midea 170L CN30 panel  http://{ns.listen}:{ns.http_port}/")
    print(f"link                   {_link_label()}  READ-ONLY")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
