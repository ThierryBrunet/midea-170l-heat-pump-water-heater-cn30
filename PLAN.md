# Plan: Chromagen Midea HP170 via EW-11A → Home Assistant

Resume workspace for the heat-pump water heater. Hardware (EW-11A, AliExpress) not on site yet.

Sister project for the mains meter: `../MainsPulsePowerMeter`.

## Goals

1. Monitor and control **Chromagen Midea 170L / HP170 / RSJ-15/190RDN3-C** over RS485.
2. Bridge: **EW-11A** RS485 → WiFi, TCP port **502**, serial **600 8N1**, UART protocol **NONE** (transparent). Not Modbus.
3. Home Assistant: `custom_components/midea_cn30_hws/` (read-only). **[0xAHA/Midea-Heat-Pump-HA](https://github.com/0xAHA/Midea-Heat-Pump-HA)** is a different main board (9600 Modbus). Do not install that profile on this dongle. No CN30 writes until a command frame is proven.
4. Until EW-11A is on LAN: MQTT **stand-in** `water_heater.chromagen_hp170` (same entity ids for later cutover). Do **not** add HACS against a dead IP.
5. 240 VAC PSU inside the HWS cover: **licensed electrician only**.

## Defaults (upstream)

Registers: power 0, mode 1 (eco=1, performance=2, electric=4), target 2, sterilize 3, temps 101–104.

## Resume checklist

- [ ] Run `sim/mqtt_mocks/hws_standin_mock.py` if dashboards should exist before hardware.
- [x] EW-11A: STA WiFi (`CourssouNetx1`), static `192.168.31.219` (`WaterHeater.lan`), Flint DHCP reservation MAC `74:e9:d8:ec:75:c0`, NTP `au.pool.ntp.org` GMT+10, 9600 RS485 half-duplex, UART protocol Modbus, TCP 502. Pinout in `docs/hardware-midea-ew11.md`.
- [x] Pre-HACS verify: `python verify/cli.py selftest` and `python verify/web_app.py` (control-panel emulator).
- [x] CN30 Wire Control path: EW-11 **600 baud**, UART **NONE**. Sniff `python verify/cn30_sniff.py`. First live capture is periodic ~28-byte packets (not yet `FE AA` 33-byte frames); try one A/B swap.
- [ ] Copy `custom_components/midea_cn30_hws` to Home Assistant, add the integration against `192.168.31.219:502`, then remove the MQTT stand-in. Do not install the 0xAHA Modbus integration.
- [ ] Optional later: pymodbus TCP mock of the register map.
