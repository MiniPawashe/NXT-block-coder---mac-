# NXT Blocks

A Scratch-style block editor for the original LEGO MINDSTORMS NXT brick, for Apple Silicon Macs.
Built on [NXT-Python](https://ni.srht.site/nxt-python/latest/). Drag blocks together, press Run, and
the program drives the brick over USB. It runs in your browser, served only on your own Mac.

## What's in the box
- Blocks for motors (A, B, C), touch sensors (ports 1, 2), a color sensor (port 3) and an
  ultrasonic sensor (port 4), plus wait, repeat, forever, if, if/else, repeat-until and beep.
- A live sensor readout, save/load of programs (JSON), and a "Show code" button that produces the
  equivalent standalone NXT-Python script.

## Install (recommended): build the Mac app
Needs an Apple Silicon Mac, macOS 11 or later, and [Homebrew](https://brew.sh).

1. Download this repository (Code > Download ZIP) and unzip it.
2. In Terminal, run: `bash build_nxt_blocks_app.sh` (or type `bash `, drag the script in, press Return).
3. When it finishes, open **NXT Blocks.app** from `~/Applications`. A shareable zip is created on your Desktop.

The app bundles its own Python, NXT-Python and libusb, so other Apple Silicon Macs need nothing extra.
It is not notarized by Apple: on another Mac, open System Settings > Privacy & Security and click
**Open Anyway** the first time (or run `xattr -dr com.apple.quarantine "/Applications/NXT Blocks.app"`).

## Run from source instead
```bash
brew install python libusb
python3 -m venv ~/nxt-env && source ~/nxt-env/bin/activate
python3 -m pip install -r requirements.txt
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
python3 nxt_blocks_v2.py
```

## Using it
Plug the NXT into USB and switch it on. Wiring is fixed: touch sensors on ports 1 and 2, color sensor
on port 3, ultrasonic sensor on port 4, motors on A, B and C. Drag blocks from the left into the
script, press **Run**, and **Stop** to halt. Press **Quit** when you are finished. Sample programs are
in `examples/` (use **Load**).

## Troubleshooting
- **Brick not found:** check the cable and power, and close any other NXT software; only one
  program can use the USB connection. Run and Sensors cannot be used at the same time.
- **"No backend" or libusb errors (running from source):** `brew install libusb` and set
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
- **App will not open or misbehaves:** see `~/Library/Logs/NXT-Blocks.log`.

## Notes
- USB only. Bluetooth did not work in testing between an original NXT and a recent macOS.
- The packaged app is for Apple Silicon Macs only.
- NXT-Python is open source (GPL, as far as I know); check its license before redistributing the built app.
