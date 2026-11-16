#!/usr/bin/env bash
# Builds a self-contained "NXT Blocks.app" (Apple Silicon) that includes its own Python,
# NXT-Python and libusb, so it can be shared with other Apple Silicon Macs.
# Run once:  bash build_nxt_blocks_app.sh
set -u

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This is for macOS."
[ "$(uname -m)" = "arm64" ] || die "Run this in a normal (not Rosetta) Terminal on an Apple Silicon Mac."
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(ls -t "$HERE"/nxt_blocks_v2*.py "$HOME"/Downloads/nxt_blocks_v2*.py "$HOME"/nxt-programs/nxt_blocks_v2*.py 2>/dev/null | head -n1)"
[ -n "$SRC" ] || die "Could not find nxt_blocks_v2.py. Put it in the same folder as this script and run again."
echo "Using $SRC"

if ! command -v brew >/dev/null 2>&1; then
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)" || die "Homebrew not found (https://brew.sh)."
fi
brew install python libusb || die "brew install failed"

PY="$(brew --prefix python)/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
LIBUSB="$(brew --prefix libusb)/lib/libusb-1.0.dylib"
[ -f "$LIBUSB" ] || die "libusb not found at $LIBUSB"

WORK="$HOME/.nxt-blocks-build"
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK" || die "Cannot create $WORK"
"$PY" -m venv venv || die "Could not create the Python environment"
./venv/bin/python -m pip install --upgrade pip nxt-python pyinstaller || die "pip install failed"
cp "$SRC" nxt_blocks.py
cp "$LIBUSB" .

echo "Packaging (this takes a minute)..."
./venv/bin/pyinstaller --noconfirm --windowed --name "NXT Blocks" \
  --osx-bundle-identifier local.nxtblocks \
  --collect-submodules nxt --collect-submodules usb \
  --add-binary "libusb-1.0.dylib:." nxt_blocks.py || die "PyInstaller failed"

APP="$WORK/dist/NXT Blocks.app"
[ -d "$APP" ] || die "The app was not created."
echo "Self-test..."
"$APP/Contents/MacOS/NXT Blocks" --selftest || die "Self-test failed: the app could not load libusb."
xattr -cr "$APP" 2>/dev/null
codesign --force --deep -s - "$APP" 2>/dev/null || echo "(ad-hoc signing skipped)"

mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/NXT Blocks.app"
cp -R "$APP" "$HOME/Applications/"
ditto -c -k --keepParent "$APP" "$HOME/Desktop/NXT-Blocks.zip"

echo
echo "Done."
echo "  Installed:  ~/Applications/NXT Blocks.app   (drag it to Applications or the Dock if you like)"
echo "  To share:   ~/Desktop/NXT-Blocks.zip   (Apple Silicon Macs only)"
open -R "$HOME/Applications/NXT Blocks.app"
