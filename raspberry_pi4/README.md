# MeridianHD — Raspberry Pi 4 Edition

A graphical HD Radio receiver application for the Raspberry Pi 4, built with Python and PySide6. Tunes HD Radio stations via an RTL-SDR dongle using `nrsc5`, displays album art, station metadata, signal quality meters, and emergency alerts — all in a dark-themed GUI.

---

## Requirements

### Hardware
- Raspberry Pi 4 (2GB RAM minimum, 4GB recommended)
- RTL-SDR USB dongle (RTL2832U-based, e.g. RTL-SDR Blog V3)
- FM antenna connected to the dongle
- Audio output device (HDMI, USB DAC, or 3.5mm headphone jack)
- Display (HDMI monitor or official Pi touchscreen)

### Operating System
- Raspberry Pi OS (Bookworm or Bullseye, 64-bit recommended)

---

## Dependencies

### System Packages

Install all system-level dependencies with a single command:

```bash
sudo apt update && sudo apt install -y \
    nrsc5 \
    rtl-sdr \
    libportaudio2 \
    libportaudiocpp0 \
    portaudio19-dev \
    python3-pip \
    python3-venv \
    libgl1 \
    libegl1
```

| Package | Purpose |
|---|---|
| `nrsc5` | HD Radio demodulator (drives the RTL-SDR dongle) |
| `rtl-sdr` | RTL-SDR drivers and tools (includes `rtl_test`) |
| `libportaudio2` | PortAudio runtime library (required by sounddevice) |
| `libportaudiocpp0` | PortAudio C++ bindings |
| `portaudio19-dev` | PortAudio development headers |
| `python3-pip` | Python package manager |
| `python3-venv` | Python virtual environment support |
| `libgl1` | OpenGL library (required by PySide6) |
| `libegl1` | EGL library (required by PySide6 on Pi) |

### Python Packages

```bash
pip3 install PySide6 sounddevice numpy
```

| Package | Purpose |
|---|---|
| `PySide6` | Qt6 GUI framework |
| `sounddevice` | Audio output via PortAudio |
| `numpy` | Audio buffer processing |

---

## Installation

**1. Clone or copy the script to your Pi:**
```bash
mkdir ~/meridianHD && cd ~/meridianHD
# Copy MeridianHD_Pi.py into this folder
```

**2. Install system dependencies:**
```bash
sudo apt update && sudo apt install -y \
    nrsc5 rtl-sdr libportaudio2 libportaudiocpp0 \
    portaudio19-dev python3-pip python3-venv libgl1 libegl1
```

**3. Install Python dependencies:**
```bash
pip3 install PySide6 sounddevice numpy
```

**4. Plug in your RTL-SDR dongle**, then verify it is detected:
```bash
rtl_test -t
```
You should see your dongle listed. Press `Ctrl+C` to stop the test.

**5. Run the application:**
```bash
python3 MeridianHD_Pi.py
```

---

## RTL-SDR Blacklist Fix

On Raspberry Pi OS, the kernel may load a conflicting DVB-T driver that prevents `nrsc5` from accessing the dongle. If you get a device error on launch, blacklist the driver:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/rtlsdr.conf
sudo reboot
```

---

## Usage

| Control | Description |
|---|---|
| **FREQ** | Set the FM frequency (87.5 – 108.5 MHz) |
| **PROG** | Select HD sub-channel (HD1 through HD4) |
| **RTL-SDR** | Choose which RTL-SDR dongle to use (if multiple are connected) |
| **OUT** | Select audio output device |
| **VOL** | Adjust playback volume |
| **SIG (MER)** | Modulation Error Ratio — signal quality indicator (green = good, red = poor) |
| **BER** | Bit Error Rate — lower is better |
| **POWER ON/OFF** | Start or stop reception |
| **PRESET → SAVE** | Save the current frequency and channel as a preset |
| **PRESET → DEL** | Delete the selected preset |
| **LOC label** | Click to copy station GPS coordinates to clipboard |

Presets are saved automatically to `presets.json` in the same folder as the script and persist between sessions.

---

## Auto-Start on Boot (Optional)

To launch MeridianHD automatically when the Pi desktop loads, create an autostart entry:

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/meridianHD.desktop
```

Paste the following, adjusting the path if needed:

```ini
[Desktop Entry]
Type=Application
Name=MeridianHD
Exec=python3 /home/ahartman/meridianHD/MeridianHD_Pi.py
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`). MeridianHD will start automatically on next login.

---

## Troubleshooting

**`PortAudio library not found`**
```bash
sudo apt install libportaudio2 libportaudiocpp0 portaudio19-dev
```

**`nrsc5: command not found`**
```bash
sudo apt install nrsc5
```
If `nrsc5` is not available in your apt repositories, build it from source:
```bash
sudo apt install cmake librtlsdr-dev
git clone https://github.com/theori-io/nrsc5.git
cd nrsc5 && mkdir build && cd build
cmake .. && make && sudo make install
```

**`No RTL-SDR devices found` / dongle not detected**
Run `rtl_test -t` to check detection. If it fails, apply the DVB-T blacklist fix described above and reboot.

**`could not connect to display` / GUI won't open**
Make sure you are running the script from within the Pi desktop environment, not over a headless SSH session. If using SSH with X forwarding:
```bash
ssh -X ahartman@meridianpi
```

**No audio output**
Open `raspi-config → System Options → Audio` and set the correct output device, or select the correct device from the **OUT** dropdown inside the app.

---

## File Structure

```
meridianHD/
├── MeridianHD_Pi.py   # Main application
├── presets.json        # Saved presets (auto-created)
└── aas/                # Album art cache (auto-created, cleared on each session)
```

---

## License

This project uses `nrsc5` for HD Radio demodulation. Please refer to the [nrsc5 repository](https://github.com/theori-io/nrsc5) for its licensing terms. RTL-SDR support is provided by the `librtlsdr` library.
