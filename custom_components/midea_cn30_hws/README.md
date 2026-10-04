# Midea Water Heat Pump (CN30)

Home Assistant custom integration for a Chromagen Midea HP170
(RSJ-15/190RDN3-C) whose main board is `R-ZT15/190(C)-A[AC128]`
(Freescale MC9S08AC128).

Status comes from the red 3-pin **Wire Control CN30** header: a 33-byte
broadcast at **600 baud, 8N1**, about every 4 seconds. The Elfin EW-11 is a
transparent TCP bridge. This is not Modbus.

[0xAHA/Midea-Heat-Pump-HA](https://github.com/0xAHA/Midea-Heat-Pump-HA) targets
a different main board (9600 8N1 Modbus, slave 1, holding registers). On this
PCB that profile times out against the CN30 stream. Do not install it on this
dongle.

## What it exposes

Read-only device:

| Entity | Source |
|---|---|
| Water heater | Mode pair bytes 5–6, target byte 29, T5L tank temperature byte 16 |
| T5L Tank, T3 Evaporator, T4 Ambient | Bytes 16, 15, and 24, scale `(raw * 0.5) - 15` |
| TP Discharge Air | Byte 22, unscaled integer °C |
| EEV | Byte 20, integer opening |
| Target temperature | Byte 29, integer °C |
| Element | Byte 21 non-zero (the element glyph; can be on while mode is Off) |
| Timer | Bit `0x10` on byte 5 or 6 |
| Keypad lock bit | Byte 11 bit `0x20` (diagnostic; the padlock glyph can stay lit after the bit clears) |

Operation modes reported: `off`, `eco`, `performance` (Hybrid), `electric` (E-Heater).

T5/T3/T4 use `(raw * 0.5) - 15`. TP is unscaled °C. Target, mode limits, and EEV are already integers. TH suction has no separate byte; the 2026-10-04 09:22 panel query had Th = T3 with the compressor off.

There is no setpoint, mode, or power control. No command frame has been accepted
on this header. The integration never writes the TCP socket. A finished 33-byte
copy while the tank is Off has latched keypad fault E2.

## EW-11 settings

These match the dongle on this heater (`192.168.31.219`):

- UART `600,8,1,NONE`
- Flow control half-duplex RS485 (FlowCtrl 2)
- UART protocol **NONE** (Modbus gateway mode splits these frames)
- GapTime **1000**
- Socket `netp`: TCP server, port **502**, transparent

On this install the EW-11 A/B pair is swapped relative to the screw labels.
Leave that swap. Data pair and GND only. Do not put the heater's VCC or a
ribbon supply on A or B.

Stop anything else that connects to port 502 before adding the integration.
A second TCP client can take the serial stream. That includes
`verify/web_app.py`.

## Install

1. Copy this folder to the Home Assistant config directory:

   `config/custom_components/midea_cn30_hws/`

2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Midea Water Heat Pump (CN30)**.
4. Host `192.168.31.219`, port `502`. Setup waits for one checksum-valid frame and does not send a command.

The water heater entity has no target or mode controls. Automations can read
`current_temperature`, `current_operation`, and the sensors. A service call that
sets temperature or mode raises an error and does not touch the bus.

Debug logging:

```yaml
logger:
  logs:
    custom_components.midea_cn30_hws: debug
```

## Why control is absent

The main controller masters CN30 and broadcasts its status. Copies of that
broadcast, short `FE AA` frames, and Modbus writes on this header did not move
byte 29 or the mode pair. Control stays off until a frame is shown to do that
on the next heater status frame. Tracking issue:
https://github.com/ThierryBrunet/midea-170l-heat-pump-water-heater-cn30/issues/1
