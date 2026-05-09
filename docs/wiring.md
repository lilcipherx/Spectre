# Hardware wiring

## Block diagram

```
Hytera PD785G ── EAN17 (cut) ── AUX 3.5mm ── USB sound card ── Raspberry Pi 4 (USB)
                                                                │
                                                                │  SPI + GPIO
                                                                ▼
                                         ILI9341 3.2" TFT (320x240 landscape)
```

## USB audio card

Any off-the-shelf USB audio card with a mic/line input works.  Plug it in and
check that it shows up:

```
$ arecord -l
card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]
```

The number after `card` determines the ALSA device string — for `card 1` the
string is `plughw:1,0`.  Set this in `.env` as `SPECTRE_AUDIO_DEVICE=plughw:1,0`.

## ILI9341 wiring (40-pin header)

| ILI9341 pin | Pi header pin | BCM GPIO | Role               |
|-------------|---------------|----------|--------------------|
| VCC         | 1 or 17       | 3.3 V    | power              |
| GND         | 6             | GND      | ground             |
| CS          | 24            | GPIO 8   | SPI0 CE0           |
| RESET       | 22            | GPIO 25  | reset (via GPIO)   |
| DC / RS     | 18            | GPIO 24  | data/command       |
| SDI (MOSI)  | 19            | GPIO 10  | SPI0 MOSI          |
| SCK         | 23            | GPIO 11  | SPI0 SCLK          |
| LED         | 1 or 17       | 3.3 V    | backlight (always on) |
| SDO (MISO)  | 21            | GPIO 9   | SPI0 MISO (optional) |

Touchscreen (XPT2046) and the SD-card slot on the display are **not wired** —
the operator reads only.

After wiring, enable SPI in `raspi-config` (or via `scripts/install.sh`) and
reboot.  Confirm `/dev/spidev0.0` exists:

```
$ ls /dev/spidev*
/dev/spidev0.0  /dev/spidev0.1
```

## Quick smoke tests

Audio:

```
$ scripts/test_mic.sh plughw:1,0
```

Display (using the bundled driver, with console fallback):

```
$ SPECTRE_DRY_RUN=1 SPECTRE_DISPLAY=console spectre --wav docs/sample_uz.wav
```
