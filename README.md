# MeridianHD

A graphical HD Radio receiver application for Windows, built on top of [nrsc5](https://github.com/theori-io/nrsc5) and an RTL-SDR dongle. MeridianHD provides a clean, dark-themed GUI for tuning FM HD Radio stations with real-time song metadata, album art, signal meters, and emergency alert display.

---

## Requirements

### Hardware
- An **RTL-SDR compatible USB dongle** (e.g. RTL2832U-based devices)

### Software Dependencies
- `nrsc5.exe` — must be placed alongside `MeridianHD.exe` in the same folder
- `rtlsdr.dll` (and `libusb-1.0.dll` if required by your RTL-SDR driver) — must also be present in the same folder

---

## Installation

1. Download or build `MeridianHD.exe` (see [Building from Source](#building-from-source) below).
2. Place the following files all in the same folder:
   - `MeridianHD.exe`
   - `nrsc5.exe`
   - `rtlsdr.dll` (and `libusb-1.0.dll` if needed)
3. Plug in your RTL-SDR dongle.
4. Launch `MeridianHD.exe`.

> The `aas\` folder (for album art cache) and `presets.json` are created automatically on first run — no manual setup needed.

---

## Usage

### Tuning a Station

- **FREQ** — Use the frequency spinner to set your desired FM frequency (87.5–108.5 MHz).
- **PROG** — Select the HD Radio sub-channel from the dropdown:
  - `HD1 (Main)` — Primary digital broadcast
  - `HD2 (Sub 1)` — First sub-channel
  - `HD3 (Sub 2)` — Second sub-channel
  - `HD4 (Sub 3)` — Third sub-channel
- Press **POWER ON** to begin receiving. The button toggles to **POWER OFF** to stop.

> Changing the PROG channel while a station is active will automatically restart the receiver on the new sub-channel.

---

### Audio Output

- **OUT** — Select your preferred audio output device from the dropdown before powering on. The system default output device is pre-selected automatically.
- The audio device selector is disabled while the receiver is active. Stop the receiver first to change devices.

---

### Volume

- Use the **VOL** slider to adjust playback volume from 0–100%. The current percentage is shown next to the label.
- Volume can be adjusted at any time, including while the receiver is running.

---

### Signal Meters

| Meter | Description |
|-------|-------------|
| **SIG (MER)** | Modulation Error Ratio in dB. Green = good (>9 dB), Red = weak signal. |
| **BER** | Bit Error Rate. Green = excellent (<0.0001), Yellow = marginal (<0.01), Red = poor. |

Both meters update in real time as the signal is received.

---

### Now Playing Display

When a station is tuned and active, the main display shows:

- **Album Art** — Displayed in the center panel (400×400). Automatically updates when the station broadcasts new artwork. Reverts to the station logo when album art expires.
- **Program Type badge** — A small green label showing the genre (e.g. Jazz, News, Rock) when broadcast by the station.
- **Song Title** — Displayed in large text in the center of the window.
- **Artist** — Displayed below the title. When no song metadata is present, the station slogan is shown instead.
- **Station Slogan** — Shown in italics below the artist when the station is broadcasting a slogan and no artist metadata is available.

---

### Station Info (SIS Data)

A data strip below the signal meters displays Station Information Service (SIS) metadata received from the broadcast:

| Field | Description |
|-------|-------------|
| **CTRY** | Country code |
| **FCC ID** | FCC facility identifier |
| **LOC** | Station geographic coordinates — **click to copy to clipboard** |
| **RATE** | Current audio bit rate |

---

### Emergency Alerts

If the station broadcasts an emergency alert, a **flashing red banner** appears at the top of the display with the alert message. The banner automatically disappears when the alert clears.

---

### Presets

MeridianHD supports up to **10 saved presets** for quick access to favourite stations.

- **Saving a preset** — Tune to a frequency and sub-channel, then click **SAVE**. You will be prompted to enter a name (defaults to the frequency and channel, e.g. `98.1 HD2`).
- **Loading a preset** — Select a preset from the **PRESET** dropdown. The frequency and channel will update immediately. If the receiver is currently running, it will switch to the new station automatically.
- **Deleting a preset** — Select the preset from the dropdown, then click **DEL**. A confirmation dialog will appear before deletion.

Presets are saved to `presets.json` in the same folder as the executable and persist between sessions.

---

### Debug Log

A scrollable console at the bottom of the window displays raw output from `nrsc5`, including signal events, metadata parsing, and any errors. This is useful for diagnosing reception issues.

---

## Building from Source

### Prerequisites

Install the required Python packages:

```
pip install PySide6 sounddevice numpy pyinstaller
```

### Steps

1. Place `MeridianHD.py`, `MeridianHD.spec`, and `nrsc5.exe` in the same folder.
2. Run:
   ```
   pyinstaller MeridianHD.spec
   ```
3. The finished build will be at:
   ```
   dist\MeridianHD\MeridianHD.exe
   ```
4. Copy `rtlsdr.dll` (and `libusb-1.0.dll` if needed) into `dist\MeridianHD\`.
5. Distribute the entire `dist\MeridianHD\` folder — it contains all required DLLs.

---

## Troubleshooting

**`[WinError 2] The system cannot find the file specified`**
`nrsc5.exe` or one of its required DLLs (`rtlsdr.dll`, `libusb-1.0.dll`) is missing from the application folder. Ensure all three files are present alongside `MeridianHD.exe`.

**No audio / silence after tuning**
- Confirm your RTL-SDR dongle is plugged in and recognised by Windows.
- Verify the correct audio output device is selected in the **OUT** dropdown.
- Check the **SIG (MER)** meter — a red reading indicates a weak or absent signal. Try a different frequency or improve your antenna.

**Album art not appearing**
Art is delivered over the air and may take 30–60 seconds to arrive after tuning. Not all stations broadcast album art.

**Sub-channels (HD2/HD3/HD4) not working**
Sub-channels are only available once a solid HD1 lock is established (MER consistently green). Weak signals may only support HD1.
