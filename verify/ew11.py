"""Elfin EW-11A HTTP status (SSID / RSSI) via /cmd GET_STATE."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_USER = os.environ.get("EW11_USER", "admin")
DEFAULT_PASS = os.environ.get("EW11_PASS", "china4ever+")


def _post(host: str, payload: dict, timeout: float) -> dict[str, Any]:
    url = f"http://{host}/cmd"
    body = ("msg=" + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
    pwd = os.environ.get("EW11_PASS", DEFAULT_PASS)
    user = os.environ.get("EW11_USER", DEFAULT_USER)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    token = (user + ":" + pwd).encode("ascii")
    req.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def wait_http(host: str, seconds: float = 45.0) -> bool:
    t_end = time.time() + seconds
    while time.time() < t_end:
        try:
            _post(host, {"CID": 10003, "PL": ["UART"]}, 8)
            return True
        except Exception:
            time.sleep(2.0)
    return False


def set_uart(
    host: str,
    *,
    baud: int = 600,
    proto: str = "NONE",
    gap: int = 1000,
    restart: bool = True,
    timeout: float = 8.0,
) -> dict[str, Any]:
    uart = {
        "Baudrate": int(baud),
        "Databits": 8,
        "Stopbits": 1,
        "Parity": "NONE",
        "BufSize": 512,
        "GapTime": int(gap),
        "FlowCtrl": 2,
        "SoftwareFlowCtrl": 0,
        "Xon": "11",
        "Xoff": "13",
        "CliGetIn": "Disable",
        "SerailString": "+++",
        "CliWaitTime": 300,
        "UartProto": proto,
        "FrameLen": 16,
        "FrameTime": 100,
        "TagEnable": 0,
        "TagHead": "00",
        "TagTail": "00",
    }
    resp = _post(host, {"CID": 10005, "PL": {"UART": uart}}, timeout)
    if restart:
        try:
            _post(host, {"CID": 20003, "PL": {}}, timeout)
        except Exception:
            pass
        wait_http(host)
    return resp


def set_uart_cn30(host: str, timeout: float = 8.0) -> dict[str, Any]:
    """600 baud, 8N1, RS485 half-duplex, transparent (not Modbus gateway)."""
    uart = {
        "Baudrate": 600,
        "Databits": 8,
        "Stopbits": 1,
        "Parity": "NONE",
        "BufSize": 512,
        "GapTime": 200,
        "FlowCtrl": 2,
        "SoftwareFlowCtrl": 0,
        "Xon": "11",
        "Xoff": "13",
        "CliGetIn": "Disable",
        "SerailString": "+++",
        "CliWaitTime": 300,
        "UartProto": "NONE",
        "FrameLen": 16,
        "FrameTime": 100,
        "TagEnable": 0,
        "TagHead": "00",
        "TagTail": "00",
    }
    resp = _post(host, {"CID": 10005, "PL": {"UART": uart}}, timeout)
    _post(host, {"CID": 20003, "PL": {}}, timeout)
    return resp


def uart_traffic(host: str, timeout: float = 8.0) -> dict[str, Any]:
    """Live UART/SOCK byte counters from GET_STATE (queried separately)."""
    u = _post(host, {"CID": 10001, "PL": ["UART"]}, timeout)
    s = _post(host, {"CID": 10001, "PL": ["SOCK"]}, timeout)
    uart = (u.get("PL") or {}).get("UART") or {}
    sock = (s.get("PL") or {}).get("SOCK")
    if isinstance(sock, list):
        sock = sock[0] if sock else {}
    return {"uart": uart, "sock": sock or {}}


def get_uart(host: str, timeout: float = 8.0) -> dict[str, Any]:
    r = _post(host, {"CID": 10003, "PL": ["UART"]}, timeout)
    return (r.get("PL") or {}).get("UART") or {}


def wifi_status(host: str, timeout: float = 4.0) -> dict[str, Any]:
    """Return STA SSID, RSSI, and link state. Never includes credentials."""
    out: dict[str, Any] = {
        "ssid": None,
        "rssi": None,
        "state": None,
        "bssid": None,
        "error": None,
    }
    try:
        st = _post(host, {"CID": 10001, "PL": ["SYS"]}, timeout)
        sys_pl = (st.get("PL") or {}).get("SYS") or {}
        out["rssi"] = sys_pl.get("WiFiRssi")
        state = sys_pl.get("WiFiState") or ""
        if "," in str(state):
            name, bssid = str(state).split(",", 1)
            out["state"] = name.strip()
            out["bssid"] = bssid.strip()
        else:
            out["state"] = state or None
        cfg = _post(host, {"CID": 10003, "PL": ["SYS"]}, timeout)
        cfg_pl = (cfg.get("PL") or {}).get("SYS") or {}
        out["ssid"] = cfg_pl.get("WiFiSTASSID") or cfg_pl.get("HostName")
    except urllib.error.HTTPError as exc:
        out["error"] = f"http {exc.code}"
    except Exception as exc:
        out["error"] = str(exc)
    return out
