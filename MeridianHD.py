import sys
import os
import time
import subprocess
import threading
import numpy as np
import glob
import json
from datetime import datetime, timezone
import sounddevice as sd
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QWidget, QTextEdit, QDoubleSpinBox,
                             QComboBox, QSlider, QProgressBar, QFrame,
                             QInputDialog, QMessageBox)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect

# Resolve the directory that contains the running executable (or script).
# When frozen by PyInstaller (--onefile), __file__ points inside a temp
# extraction folder; sys.executable always points to the actual .exe location.
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

AAS_PATH     = os.path.join(_APP_DIR, "aas")
PRESETS_FILE = os.path.join(_APP_DIR, "presets.json")
MAX_PRESETS  = 10

# Path to nrsc5.exe — expected to live alongside the executable
NRSC5_EXE = os.path.join(_APP_DIR, "nrsc5.exe") if sys.platform == 'win32' else "nrsc5"

class NRSC5Manager(QThread):
    sig_log = Signal(str)
    sig_meta = Signal(str, str)
    sig_mer = Signal(float)
    sig_station = Signal(str, str, str, str)
    sig_art = Signal(str)        # Emits absolute path to an image file
    sig_art_expiry = Signal(str) # Emits ISO8601 expiry timestamp string for album art
    sig_ber = Signal(float)      # Bit Error Rate
    sig_pty = Signal(str)        # Program Type string (e.g. "Jazz", "News")
    sig_slogan = Signal(str)     # Station slogan
    sig_alert = Signal(str)      # Emergency alert message (empty string = clear)

    def __init__(self, frequency, program_index, device_index=None):
        super().__init__()
        self.freq = frequency
        self.prog = program_index
        self.process = None
        self._is_running = True
        self.volume = 1.0
        self.stream = sd.OutputStream(samplerate=44100, channels=2, dtype='int16',
                                      device=device_index)
        os.makedirs(AAS_PATH, exist_ok=True)
        self._art_port_map = {}        # prog_index -> album art LOT port (even)
        self._logo_port_map = {}       # prog_index -> station logo LOT port (odd)
        self._sig_current_program = None  # tracks which SIG service block we're inside
        self._station_logo_path = None    # most recently received station logo
        self._art_expiry = None           # datetime of current album art expiry

    def _wait_for_file(self, path, retries=10, delay=0.3):
        """Poll for a file to appear and have non-zero size (nrsc5 may still be writing it)."""
        for _ in range(retries):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True
            time.sleep(delay)
        return False

    def _emit_newest_image(self):
        """Fallback: find the most recently modified image in AAS_PATH and emit it."""
        try:
            candidates = glob.glob(os.path.join(AAS_PATH, "*.jpg")) +                          glob.glob(os.path.join(AAS_PATH, "*.jpeg")) +                          glob.glob(os.path.join(AAS_PATH, "*.png"))
            if candidates:
                newest = max(candidates, key=os.path.getmtime)
                if os.path.getsize(newest) > 0:
                    self.sig_log.emit(f"[AAS-FALLBACK] Emitting newest file: {newest}")
                    self.sig_art.emit(newest)
                else:
                    self.sig_log.emit("[AAS-FALLBACK] Newest file has zero size, skipping.")
            else:
                self.sig_log.emit(f"[AAS-FALLBACK] No image files found in {AAS_PATH}")
        except Exception as e:
            self.sig_log.emit(f"[AAS-FALLBACK-ERROR] {e}")

    def log_reader(self):
        """Live parsing for SIS, Bitrate, Metadata, and LOT (Artwork)."""
        last_title, last_artist = "", ""
        country, fcc_id, location, bitrate = "---", "---", "---", "---"

        for line in iter(self.process.stderr.readline, b''):
            if not self._is_running:
                break
            decoded = line.decode('utf-8', errors='replace').strip()
            if not decoded:
                continue

            self.sig_log.emit(decoded)

            # ----------------------------------------------------------------
            # AAS / LOT Artwork Detection
            # Per program, two LOT ports exist (derived from SIG definitions):
            #   Even port (1000, 1002, 1004...): Album art — has expiry timestamp
            #   Odd port  (1001, 1003, 1005...): Station logo — permanent fallback
            # _logo_port_map : prog -> odd port  (station logo)
            # _art_port_map  : prog -> even port (album art)
            # ----------------------------------------------------------------

            # --- SIG parsing: build both port maps dynamically ---
            if "SIG Service:" in decoded and "type=audio" in decoded:
                try:
                    num = int(decoded.split("number=")[1].split()[0])
                    self._sig_current_program = num - 1  # 0-based
                except Exception:
                    self._sig_current_program = None

            if "Data component:" in decoded and getattr(self, "_sig_current_program", None) is not None:
                try:
                    port_str_raw = decoded.split("port=")[1].split()[0]
                    port_decimal = int(port_str_raw, 10)  # SIG hex digits treated as decimal face value
                    if "mime=BE4B7536" in decoded:
                        self._art_port_map[self._sig_current_program] = port_decimal
                    elif "mime=D9C72536" in decoded:
                        self._logo_port_map[self._sig_current_program] = port_decimal
                except Exception as e:
                    self.sig_log.emit(f"[SIG-MAP] parse error: {e} | {decoded}")

            if "LOT file:" in decoded:
                try:
                    tokens = {}
                    for token in decoded.split():
                        if "=" in token:
                            k, _, v = token.partition("=")
                            tokens[k.lower()] = v.rstrip(",;\"'")

                    port_str  = tokens.get("port", "")
                    fname     = tokens.get("name", "")
                    lot_str   = tokens.get("lot", "")
                    expiry_str = tokens.get("expiry", "")

                    if not port_str or not fname:
                        pass
                    else:
                        port_val = int(port_str, 10)

                        # Determine which port belongs to which role for this program
                        if self._art_port_map:
                            art_port  = self._art_port_map.get(self.prog, -1)
                            logo_port = self._logo_port_map.get(self.prog, -1)
                        else:
                            # Formula fallback
                            art_port  = 1000 + (self.prog * 2)
                            logo_port = 1001 + (self.prog * 2)

                        if port_val not in (art_port, logo_port):
                            pass  # belongs to a different sub-channel, ignore
                        else:
                            fname = os.path.basename(fname)
                            prefixed = f"{lot_str}_{fname}" if lot_str else fname
                            candidates_to_try = [
                                os.path.join(AAS_PATH, prefixed),
                                os.path.join(AAS_PATH, fname),
                            ]
                            found_path = None
                            for candidate in candidates_to_try:
                                if self._wait_for_file(candidate):
                                    found_path = candidate
                                    break

                            if found_path:
                                if port_val == logo_port:
                                    # Station logo — store it and display if no art active
                                    self._station_logo_path = found_path
                                    if not self._art_expiry:
                                        self.sig_art.emit(found_path)
                                elif port_val == art_port:
                                    # Album art — display it and schedule expiry revert
                                    self.sig_art.emit(found_path)
                                    if expiry_str:
                                        self.sig_art_expiry.emit(expiry_str)
                            else:
                                self.sig_log.emit(f"AAS: file not found, tried: {candidates_to_try}")

                except Exception as e:
                    self.sig_log.emit(f"AAS Error: {e}")

            # ----------------------------------------------------------------
            # Metadata / Signal parsing
            # ----------------------------------------------------------------
            if "Title:" in decoded:
                last_title = decoded.split("Title:", 1)[1].strip()
            elif "Artist:" in decoded:
                last_artist = decoded.split("Artist:", 1)[1].strip()

            # BER
            if "BER:" in decoded:
                try:
                    ber_val = float(decoded.split("BER:")[1].split(",")[0].strip())
                    self.sig_ber.emit(ber_val)
                except Exception:
                    pass

            # Program Type  —  nrsc5 logs: "Audio service: program=0 ... type=Jazz ..."
            if "Audio service:" in decoded and "type=" in decoded:
                try:
                    pty = decoded.split("type=")[1].split()[0].strip().rstrip(",")
                    if pty and pty != "0":
                        self.sig_pty.emit(pty)
                except Exception:
                    pass

            # Station Slogan  —  nrsc5 logs: "Slogan: <text>"
            if "Slogan:" in decoded:
                try:
                    slogan = decoded.split("Slogan:", 1)[1].strip()
                    if slogan:
                        self.sig_slogan.emit(slogan)
                except Exception:
                    pass

            # Emergency Alert  —  nrsc5 logs: "Alert: <message>" inside SIS lines
            if "Alert:" in decoded:
                try:
                    alert_text = decoded.split("Alert:", 1)[1].strip()
                    self.sig_alert.emit(alert_text)
                except Exception:
                    pass

            if "MER:" in decoded:
                try:
                    val = float(decoded.split("MER:")[1].split("dB")[0].strip())
                    self.sig_mer.emit(val)
                except Exception:
                    pass

            if "Audio bit rate:" in decoded:
                bitrate = decoded.split("Audio bit rate:", 1)[1].strip()
            if "Country:" in decoded:
                parts = decoded.split(",")
                country = parts[0].split("Country:", 1)[1].strip()
                if len(parts) > 1 and "FCC facility ID:" in parts[1]:
                    fcc_id = parts[1].split("FCC facility ID:", 1)[1].strip()
            if "Station location:" in decoded:
                location = decoded.split("Station location:", 1)[1].strip()

            if last_title or last_artist:
                self.sig_meta.emit(last_title, last_artist)

            self.sig_station.emit(country, fcc_id, location, bitrate)

    def run(self):
        # KEY FIX: --dump-aas-files tells nrsc5 to actually write LOT image files
        # to disk in the CWD (which we set to AAS_PATH below).
        # Note: older nrsc5 builds use --aas-out-dir=<path>; we try the modern flag
        # first. If your build doesn't support it, swap to the alternate form below.
        cmd = [
            NRSC5_EXE,
            "--dump-aas-files", AAS_PATH,  # directory argument required by this build
            "-o", "-",
            str(self.freq),
            str(self.prog)
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            threading.Thread(target=self.log_reader, daemon=True).start()
            self.stream.start()

            while self._is_running and self.process.poll() is None:
                raw_audio = self.process.stdout.read(4096)
                if not raw_audio:
                    break
                audio_array = np.frombuffer(raw_audio, dtype=np.int16)
                if len(audio_array) > 0:
                    audio_array = (audio_array * self.volume).astype(np.int16)
                    try:
                        self.stream.write(audio_array.reshape(-1, 2))
                    except Exception:
                        pass
        except Exception as e:
            self.sig_log.emit(f"Error: {str(e)}")

    def stop(self):
        self._is_running = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except Exception:
                self.process.kill()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.wait()


class ClickableLabel(QLabel):
    def mousePressEvent(self, event):
        if "---" not in self.text():
            clean_val = self.text().split(": ", 1)[1]
            QApplication.clipboard().setText(clean_val)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeridianHD")
        self.setMinimumWidth(800)
        self.setMinimumHeight(700)
        self.setStyleSheet("QMainWindow { background-color: #000; } QLabel { color: #FFF; font-weight: bold; }")

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)

        # --- Presets Row ---
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("PRESET:"))
        self.combo_preset = QComboBox()
        self.combo_preset.setStyleSheet("background: #111; color: #0F0; font-size: 11pt; border: 1px solid #333;")
        self.combo_preset.setMinimumWidth(220)
        self.combo_preset.currentIndexChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self.combo_preset, stretch=1)
        self.btn_preset_save = QPushButton("SAVE")
        self.btn_preset_save.setFixedWidth(60)
        self.btn_preset_save.setStyleSheet("QPushButton { background: #020; color: #0F0; font-size: 10pt; border: 1px solid #040; padding: 2px; }")
        self.btn_preset_save.clicked.connect(self._save_preset)
        self.btn_preset_del = QPushButton("DEL")
        self.btn_preset_del.setFixedWidth(50)
        self.btn_preset_del.setStyleSheet("QPushButton { background: #200; color: #F44; font-size: 10pt; border: 1px solid #400; padding: 2px; }")
        self.btn_preset_del.clicked.connect(self._delete_preset)
        preset_layout.addWidget(self.btn_preset_save)
        preset_layout.addWidget(self.btn_preset_del)
        layout.addLayout(preset_layout)

        # --- Tuning & Meters ---
        tune_layout = QHBoxLayout()
        self.spin_freq = QDoubleSpinBox()
        self.spin_freq.setRange(87.5, 108.5)
        self.spin_freq.setValue(88.1)
        self.spin_freq.setStyleSheet("background: #111; color: #0F0; font-size: 16pt; border: 1px solid #333;")
        self.combo_chan = QComboBox()
        self.combo_chan.addItems(["HD1 (Main)", "HD2 (Sub 1)", "HD3 (Sub 2)", "HD4 (Sub 3)"])
        self.combo_chan.setStyleSheet("background: #111; color: #0F0; font-size: 16pt; border: 1px solid #333;")
        self.combo_chan.currentIndexChanged.connect(self.trigger_switch)
        tune_layout.addWidget(QLabel("FREQ:"))
        tune_layout.addWidget(self.spin_freq)
        tune_layout.addSpacing(20)
        tune_layout.addWidget(QLabel("PROG:"))
        tune_layout.addWidget(self.combo_chan)
        layout.addLayout(tune_layout)

        # --- Audio Device Selection ---
        dev_layout = QHBoxLayout()
        dev_layout.addWidget(QLabel("OUT:"))
        self.combo_device = QComboBox()
        self.combo_device.setStyleSheet("background: #111; color: #0F0; font-size: 11pt; border: 1px solid #333;")
        self._audio_devices = []  # list of (index, name) for output devices
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d['max_output_channels'] > 0:
                    self._audio_devices.append((i, d['name']))
                    self.combo_device.addItem(d['name'])
            # Pre-select the system default output device
            default_out = sd.default.device[1]
            for combo_idx, (dev_idx, _) in enumerate(self._audio_devices):
                if dev_idx == default_out:
                    self.combo_device.setCurrentIndex(combo_idx)
                    break
        except Exception as e:
            self.combo_device.addItem("Default")
            self._audio_devices = [(None, "Default")]
        dev_layout.addWidget(self.combo_device, stretch=1)
        layout.addLayout(dev_layout)

        info_headers = QHBoxLayout()
        sig_col = QVBoxLayout()
        sig_head = QHBoxLayout()
        sig_head.addWidget(QLabel("SIG (MER):"))
        self.lbl_mer_val = QLabel("0.0 dB")
        self.lbl_mer_val.setStyleSheet("color: #0F0; font-size: 12pt;")
        sig_head.addWidget(self.lbl_mer_val)
        sig_col.addLayout(sig_head)
        self.mer_bar = QProgressBar()
        self.mer_bar.setRange(0, 20)
        self.mer_bar.setTextVisible(False)
        self.mer_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #333; background: #111; height: 10px; } "
            "QProgressBar::chunk { background-color: #0F0; }"
        )
        sig_col.addWidget(self.mer_bar)
        info_headers.addLayout(sig_col)
        info_headers.addSpacing(20)

        ber_col = QVBoxLayout()
        ber_head = QHBoxLayout()
        ber_head.addWidget(QLabel("BER:"))
        self.lbl_ber_val = QLabel("0.000000")
        self.lbl_ber_val.setStyleSheet("color: #0F0; font-size: 12pt;")
        ber_head.addWidget(self.lbl_ber_val)
        ber_col.addLayout(ber_head)
        self.ber_bar = QProgressBar()
        self.ber_bar.setRange(0, 100)
        self.ber_bar.setValue(0)
        self.ber_bar.setTextVisible(False)
        self.ber_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #333; background: #111; height: 10px; } "
            "QProgressBar::chunk { background-color: #0F0; }"
        )
        ber_col.addWidget(self.ber_bar)
        info_headers.addLayout(ber_col)
        info_headers.addSpacing(40)

        vol_col = QVBoxLayout()
        vol_head = QHBoxLayout()
        vol_head.addWidget(QLabel("VOL:"))
        self.lbl_vol_pct = QLabel("85%")
        self.lbl_vol_pct.setStyleSheet("color: #0F0;")
        vol_head.addWidget(self.lbl_vol_pct)
        vol_col.addLayout(vol_head)
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(85)
        self.vol_slider.valueChanged.connect(self.update_volume)
        vol_col.addWidget(self.vol_slider)
        info_headers.addLayout(vol_col)
        layout.addLayout(info_headers)

        # --- SIS Data Frame ---
        self.sis_frame = QFrame()
        self.sis_frame.setStyleSheet("QFrame { background: #080808; border: 1px solid #222; border-radius: 3px; }")
        sis_box = QHBoxLayout(self.sis_frame)
        self.lbl_country = QLabel("CTRY: ---")
        self.lbl_fccid = QLabel("FCC ID: ---")
        self.lbl_bitrate = QLabel("RATE: ---")
        self.lbl_location = ClickableLabel("LOC: ---")
        self.lbl_location.setToolTip("Click to copy coordinates")
        for lbl in [self.lbl_country, self.lbl_fccid, self.lbl_bitrate, self.lbl_location]:
            lbl.setStyleSheet("color: #0A0; font-size: 9pt; font-family: 'Consolas'; border: none;")
            sis_box.addWidget(lbl)
            sis_box.addStretch()
        layout.addWidget(self.sis_frame)

        layout.addSpacing(10)

        # --- Emergency Alert Banner (hidden until an alert arrives) ---
        self.alert_banner = QFrame()
        self.alert_banner.setStyleSheet(
            "QFrame { background: #8B0000; border: 2px solid #FF0000; border-radius: 4px; }"
        )
        alert_inner = QHBoxLayout(self.alert_banner)
        alert_inner.setContentsMargins(10, 6, 10, 6)
        self.lbl_alert_icon = QLabel("⚠ EMERGENCY ALERT")
        self.lbl_alert_icon.setStyleSheet("color: #FFD700; font-size: 11pt; font-weight: bold; border: none;")
        self.lbl_alert_text = QLabel("")
        self.lbl_alert_text.setStyleSheet("color: #FFF; font-size: 10pt; border: none;")
        self.lbl_alert_text.setWordWrap(True)
        alert_inner.addWidget(self.lbl_alert_icon)
        alert_inner.addWidget(self.lbl_alert_text, stretch=1)
        self.alert_banner.setVisible(False)
        layout.addWidget(self.alert_banner)

        # --- Artwork Display ---
        self.lbl_art = QLabel()
        self.lbl_art.setFixedSize(400, 400)
        self.lbl_art.setAlignment(Qt.AlignCenter)
        self.lbl_art.setStyleSheet("background-color: #050505; border: 1px solid #222; border-radius: 4px;")
        layout.addWidget(self.lbl_art, alignment=Qt.AlignCenter)

        layout.addSpacing(6)

        # --- Program Type badge ---
        self.lbl_pty = QLabel("")
        self.lbl_pty.setAlignment(Qt.AlignCenter)
        self.lbl_pty.setStyleSheet(
            "color: #000; background: #0A0; font-size: 9pt; font-weight: bold; "
            "border-radius: 3px; padding: 2px 8px; border: none;"
        )
        self.lbl_pty.setVisible(False)
        layout.addWidget(self.lbl_pty, alignment=Qt.AlignCenter)

        layout.addSpacing(4)

        # --- Artist / Title ---
        self.lbl_title = QLabel("SYSTEM IDLE")
        self.lbl_title.setStyleSheet("font-size: 36pt; color: #0F0;")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_title)

        # Slogan shown in place of artist when no song metadata is present
        self.lbl_artist = QLabel("Standing by...")
        self.lbl_artist.setStyleSheet("font-size: 20pt; color: #BBB;")
        self.lbl_artist.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_artist)

        self.lbl_slogan = QLabel("")
        self.lbl_slogan.setStyleSheet("font-size: 13pt; color: #555; font-style: italic;")
        self.lbl_slogan.setAlignment(Qt.AlignCenter)
        self.lbl_slogan.setVisible(False)
        layout.addWidget(self.lbl_slogan)

        layout.addSpacing(10)

        # Flash timer for alert banner
        self._alert_flash_timer = QTimer(self)
        self._alert_flash_timer.setInterval(600)
        self._alert_flash_timer.timeout.connect(self._flash_alert)
        self._alert_flash_state = False
        self._station_slogan = ""   # stored slogan, shown when no artist present

        # --- Log & Controls ---
        self.debug_log = QTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setFixedHeight(120)
        self.debug_log.setStyleSheet("background: #050505; color: #080; font-family: 'Consolas'; border: 1px solid #111;")
        layout.addWidget(self.debug_log)

        self.btn_toggle = QPushButton("POWER ON")
        self.btn_toggle.setFixedHeight(60)
        self.btn_toggle.setStyleSheet("QPushButton { font-size: 16pt; background: #030; color: #FFF; border: 1px solid #060; }")
        self.btn_toggle.clicked.connect(self.handle_toggle)
        layout.addWidget(self.btn_toggle)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.worker = None
        self._presets = []   # list of {"name": str, "freq": float, "prog": int}
        self._loading_preset = False  # guard against recursive signals
        self._load_presets()
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.timeout.connect(self._revert_to_logo)

    def update_volume(self, val):
        self.lbl_vol_pct.setText(f"{val}%")
        if self.worker:
            self.worker.volume = val / 100.0

    def update_mer(self, val):
        self.mer_bar.setValue(int(val))
        self.lbl_mer_val.setText(f"{val:.1f} dB")
        self.lbl_mer_val.setStyleSheet(f"color: {'#0F0' if val > 9.0 else '#F00'}; font-size: 12pt;")

    def update_ber(self, val):
        self.lbl_ber_val.setText(f"{val:.6f}")
        color = "#0F0" if val < 0.0001 else "#FF0" if val < 0.01 else "#F00"
        self.lbl_ber_val.setStyleSheet(f"color: {color}; font-size: 12pt;")
        # Scale BER bar: 0.0=empty, 0.01+=full (log-ish feel, capped at 100)
        bar_val = min(100, int(val * 10000))
        self.ber_bar.setValue(bar_val)
        self.ber_bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid #333; background: #111; height: 10px; }} "
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def update_meta(self, title, artist):
        self.lbl_title.setText(title if title else "---")
        if artist:
            self.lbl_artist.setText(artist)
            self.lbl_artist.setVisible(True)
            # Only hide slogan if we have real artist metadata
            self.lbl_slogan.setVisible(False)
        else:
            # No artist — show slogan if we have one
            self.lbl_artist.setVisible(False)
            if self._station_slogan:
                self.lbl_slogan.setText(self._station_slogan)
                self.lbl_slogan.setVisible(True)

    def update_pty(self, pty):
        # Don't show generic/undefined types
        if pty.upper() in ("UNDEFINED", "0", ""):
            self.lbl_pty.setVisible(False)
        else:
            self.lbl_pty.setText(pty.upper())
            self.lbl_pty.setVisible(True)

    def update_slogan(self, slogan):
        self._station_slogan = slogan
        self.lbl_slogan.setText(slogan)
        # Show slogan only when artist label is not showing real metadata
        if not self.lbl_artist.isVisible():
            self.lbl_slogan.setVisible(True)

    def update_alert(self, message):
        if message:
            self.lbl_alert_text.setText(message)
            self.alert_banner.setVisible(True)
            self._alert_flash_timer.start()
        else:
            self.alert_banner.setVisible(False)
            self._alert_flash_timer.stop()

    def _flash_alert(self):
        self._alert_flash_state = not self._alert_flash_state
        color = "#AA0000" if self._alert_flash_state else "#8B0000"
        self.alert_banner.setStyleSheet(
            f"QFrame {{ background: {color}; border: 2px solid #FF0000; border-radius: 4px; }}"
        )

    def update_sis(self, country, fcc_id, location, bitrate):
        self.lbl_country.setText(f"CTRY: {country}")
        self.lbl_fccid.setText(f"FCC ID: {fcc_id}")
        self.lbl_location.setText(f"LOC: {location}")
        self.lbl_bitrate.setText(f"RATE: {bitrate}")

    def update_art(self, path):
        pix = QPixmap(path)
        if not pix.isNull():
            self.lbl_art.setPixmap(
                pix.scaled(self.lbl_art.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def purge_assets(self):
        self._expiry_timer.stop()
        self._alert_flash_timer.stop()
        self.alert_banner.setVisible(False)
        self.lbl_pty.setVisible(False)
        self.lbl_slogan.setVisible(False)
        self._station_slogan = ""
        self.lbl_art.clear()
        if os.path.exists(AAS_PATH):
            for f in glob.glob(os.path.join(AAS_PATH, "*")):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def kill_ghosts(self):
        if sys.platform == 'win32':
            subprocess.run(
                ["taskkill", "/F", "/IM", "nrsc5.exe", "/T"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

    def trigger_switch(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.kill_ghosts()
            self.purge_assets()
            time.sleep(1.8)
            self.start_worker()

    def handle_toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.kill_ghosts()
            self.purge_assets()
            self.btn_toggle.setText("POWER ON")
            self.lbl_title.setText("OFFLINE")
            self.combo_device.setEnabled(True)
            self.combo_device.setStyleSheet("background: #111; color: #0F0; font-size: 11pt; border: 1px solid #333;")
        else:
            self.kill_ghosts()
            self.purge_assets()
            self.start_worker()
            self.btn_toggle.setText("POWER OFF")
            self.combo_device.setEnabled(False)
            self.combo_device.setStyleSheet("background: #0a0a0a; color: #444; font-size: 11pt; border: 1px solid #222;")

    def handle_art_expiry(self, expiry_str):
        """Parse expiry timestamp and start a countdown to revert to station logo."""
        try:
            expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            ms_remaining = int((expiry_dt - now).total_seconds() * 1000)
            if ms_remaining > 0:
                self._expiry_timer.start(ms_remaining)
            else:
                self._revert_to_logo()
        except Exception as e:
            self.debug_log.append(f"Expiry parse error: {e}")

    def _revert_to_logo(self):
        """Called when album art expires — revert to station logo."""
        if self.worker and self.worker._station_logo_path:
            self.update_art(self.worker._station_logo_path)

    # ----------------------------------------------------------------
    # Preset management
    # ----------------------------------------------------------------
    def _load_presets(self):
        """Load presets from JSON file and populate the dropdown."""
        self._presets = []
        if os.path.exists(PRESETS_FILE):
            try:
                with open(PRESETS_FILE, 'r') as f:
                    self._presets = json.load(f)
            except Exception:
                self._presets = []
        self._refresh_preset_combo()

    def _save_presets_file(self):
        try:
            with open(PRESETS_FILE, 'w') as f:
                json.dump(self._presets, f, indent=2)
        except Exception as e:
            self.debug_log.append(f"Preset save error: {e}")

    def _refresh_preset_combo(self):
        """Rebuild the dropdown from self._presets without triggering load."""
        self._loading_preset = True
        self.combo_preset.clear()
        self.combo_preset.addItem("-- Select Preset --")
        hd_names = ["HD1", "HD2", "HD3", "HD4"]
        for p in self._presets:
            label = f"{p['name']}  ({p['freq']:.1f} {hd_names[p['prog']]})"
            self.combo_preset.addItem(label)
        self._loading_preset = False

    def _on_preset_selected(self, index):
        if self._loading_preset or index <= 0:
            return
        preset = self._presets[index - 1]
        # Block combo_chan signal temporarily so it doesn't trigger a mid-load switch
        self.combo_chan.blockSignals(True)
        self.spin_freq.setValue(preset['freq'])
        self.combo_chan.setCurrentIndex(preset['prog'])
        self.combo_chan.blockSignals(False)

    def _save_preset(self):
        _dialog_style = (
            "QDialog, QInputDialog { background: #111; color: #FFF; }"
            "QLabel { color: #FFF; font-weight: normal; }"
            "QLineEdit { background: #1a1a1a; color: #0F0; border: 1px solid #333; padding: 4px; font-size: 12pt; }"
            "QPushButton { background: #222; color: #0F0; border: 1px solid #333; padding: 4px 12px; }"
            "QPushButton:hover { background: #2a2a2a; }"
        )
        if len(self._presets) >= MAX_PRESETS:
            msg = QMessageBox(self)
            msg.setWindowTitle("Preset List Full")
            msg.setText(f"Maximum of <b>{MAX_PRESETS}</b> presets reached.")
            msg.setInformativeText("Delete an existing preset to make room.")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.setStyleSheet(_dialog_style)
            msg.exec()
            return
        freq = self.spin_freq.value()
        prog = self.combo_chan.currentIndex()
        hd_names = ["HD1", "HD2", "HD3", "HD4"]
        default_name = f"{freq:.1f} {hd_names[prog]}"
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Save Preset")
        dlg.setLabelText("Preset name:")
        dlg.setTextValue(default_name)
        dlg.setStyleSheet(_dialog_style)
        ok = dlg.exec()
        name = dlg.textValue()
        if not ok or not name.strip():
            return
        self._presets.append({"name": name.strip(), "freq": freq, "prog": prog})
        self._save_presets_file()
        self._refresh_preset_combo()
        # Select the newly saved preset
        self.combo_preset.setCurrentIndex(len(self._presets))

    def _delete_preset(self):
        idx = self.combo_preset.currentIndex()
        if idx <= 0:
            msg = QMessageBox(self)
            msg.setWindowTitle("No Preset Selected")
            msg.setText("Please select a preset from the list to delete.")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.setStyleSheet(
                "QMessageBox { background: #111; } QLabel { color: #FFF; font-weight: normal; }"
                "QPushButton { background: #222; color: #0F0; border: 1px solid #333; padding: 4px 12px; }"
            )
            msg.exec()
            return
        preset = self._presets[idx - 1]
        hd_names = ["HD1", "HD2", "HD3", "HD4"]
        freq_str = f"{preset['freq']:.1f} {hd_names[preset['prog']]}"
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Preset")
        msg.setText(f"Are you sure you want to delete <b>{preset['name']}</b>?")
        msg.setInformativeText(f"Frequency: {freq_str}")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.setStyleSheet("QMessageBox { background: #111; color: #FFF; } QLabel { color: #FFF; font-weight: normal; } QPushButton { background: #222; color: #0F0; border: 1px solid #333; padding: 4px 12px; }")
        reply = msg.exec()
        if reply == QMessageBox.Yes:
            self._presets.pop(idx - 1)
            self._save_presets_file()
            self._refresh_preset_combo()
            self.combo_preset.setCurrentIndex(0)

    def start_worker(self):
        # Resolve selected audio output device index
        combo_idx = self.combo_device.currentIndex()
        if combo_idx >= 0 and combo_idx < len(self._audio_devices):
            dev_index = self._audio_devices[combo_idx][0]
        else:
            dev_index = None
        self.worker = NRSC5Manager(self.spin_freq.value(), self.combo_chan.currentIndex(), device_index=dev_index)
        self.worker.sig_log.connect(self.debug_log.append)
        self.worker.sig_meta.connect(self.update_meta)
        self.worker.sig_mer.connect(self.update_mer)
        self.worker.sig_ber.connect(self.update_ber)
        self.worker.sig_station.connect(self.update_sis)
        self.worker.sig_art.connect(self.update_art)
        self.worker.sig_art_expiry.connect(self.handle_art_expiry)
        self.worker.sig_pty.connect(self.update_pty)
        self.worker.sig_slogan.connect(self.update_slogan)
        self.worker.sig_alert.connect(self.update_alert)
        self.worker.volume = self.vol_slider.value() / 100.0
        self.worker.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
