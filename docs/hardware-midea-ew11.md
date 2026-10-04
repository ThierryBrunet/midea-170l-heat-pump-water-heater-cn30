# Chromagen Midea HP170 + EW-11A

Use `custom_components/midea_cn30_hws` (read-only CN30 over the EW-11 TCP bridge). Do not install [0xAHA/Midea-Heat-Pump-HA](https://github.com/0xAHA/Midea-Heat-Pump-HA): that integration is Modbus on a different main board.

## Electrical

Licensed electrician only for the 240 VAC to DC PSU inside the HWS cover.

EW-11A 4-pin (looking down at screws, stock unit):

1. Black — RS485 A  
2. Grey — RS485 B  
3. Red — VCC 5–18 V  
4. Black — GND (PSU + heater yellow GND)

Cable-with-reset variant is A / VCC / GND / B. Confirm against [upstream README_Hardware](https://github.com/0xAHA/Midea-Heat-Pump-HA/blob/main/README_Hardware.md).

## EW-11A

Live device (2026-09-21):

- Hostname `WaterHeater` / `WaterHeater.lan` → **192.168.31.219** (WAN DHCP **off**, static)
- GL-MT6000 DHCP reservation: MAC `74:e9:d8:ec:75:c0` → `192.168.31.219` (`dhcp.@host[37]`)
- STA SSID `CourssouNetx1`, web `http://192.168.31.219/` (`admin` / device password)
- Serial: **600 8N1**, flow control **half-duplex (RS485)**, UART protocol **NONE** (CN30 Wire Control is not Modbus)
- Socket `netp`: TCP server, local port **502** (raw serial tunnel)
- CN30: 33-byte frames `FE AA 00 00 FF … 55` ~every 4 s; sniff with `python verify/cn30_sniff.py`
- TX: UART **does** send (SentFrames/SendBytes rise; 33-byte frames). MCU does not treat cloned `FE AA 00 00 FF` status as a command.
- Live RX is only that one 33-byte opcode (no `AA C0` / XYE `C3` on the wire). `verify/cn30_opcode_scan.py` tried byte2 = 01/02/10/20/80/C0/C3/AA/FF, byte4 = 00/80, SOF `AA FE`, and a 16-byte XYE query — **no setpoint echo**.
- Command path is likely the **front display ribbon**, not CN30. Do not opcode-spray while the element can run.
- Display uses **two** ribbons (7-pin and 8-pin). No public pin-out found (not A/B silk). Typical split: one ribbon LCD glass/backlight, one keys/UART. Meter before tapping; ~10 V AC is power.
- CN30 brute (`verify/cn30_bruteforce.py`): 600-baud Modbus RTU FC6/16 + XYE C0/C3 + cloned status; then UART **9600 Modbus**, **9600/4800/2400/1200/19200** transparent. **D30 never moved** (stayed 61 °C E-Heater). EW-11 UART TX still increments. Treat CN30 as status-only on this PCB.

## Display ribbon capture (next)

This PC has no USB-UART/RS485 right now (only Bluetooth COM3/COM4). Capture needs a tap **on the display cable**, not another CN30 clone.

1. Isolator **off**. Do not tap 240 V.
2. Display link is the **black connector toward the front fascia**, not red **Wire Control CN30**.
3. **Meter first** (isolator on only for this check, one hand, insulated probes):
   - ~**10 V AC** on a pair → display **power**, not data. Do not put that on EW-11 A/B.
   - Quiet **~5 V DC** that twitches when you press a key → UART/TTL candidate. USB-UART **RX + GND only** (3.3/5 V, no 12 V).
   - Differential pair vs GND like CN30 → USB-**RS485**, 600–9600 8N1.
4. Prefer a **high-Z listen** (RX-only / analyzer). Cutting the ribbon causes **E2** (display ↔ main PCB).
5. After the tap is in and the display still works (no E2), from the repo:

```powershell
pip install pyserial
python verify/display_sniff.py --com COM5 --seconds 90
```

`--baud 0` (default) sweeps 600…19200. Press Temp ± / mode during each baud. CN30 is logged in parallel so we can line up button presses with MCU status. Logs: `verify/captures/display-*.log`.
- DNS `192.168.31.1`, NTP **on** (`au.pool.ntp.org:123`, GMT+10 AEST; no DST), telnet off, web port 80

Factory AP (only if you factory-reset): hotspot `EWxxxxx`, UI `10.10.100.254`, `admin`/`admin`.

HA: copy `custom_components/midea_cn30_hws` into Home Assistant. Host **192.168.31.219**, TCP **502**, read-only. Not Modbus unit 1.

Until then, run `sim/mqtt_mocks/hws_standin_mock.py` for `water_heater.chromagen_hp170`.
