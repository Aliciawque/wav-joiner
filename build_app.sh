#!/bin/bash
# Builds "WAV Joiner.app" from the files in this folder (needs the Command Line Tools).
# Produces a universal app that runs on Apple silicon and Intel Macs.
set -e
cd "$(dirname "$0")"
APP="WAV Joiner.app"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Apple's Command Line Tools first. Re-run this script when that finishes."
  xcode-select --install
  exit 1
fi
rm -rf "$APP" build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build
# Intel slice targets macOS 13: older Intel targets need Swift back-deploy
# libraries the Command Line Tools no longer ship.
swiftc -O -target arm64-apple-macos11 app/main.swift -framework AppKit -o build/arm64
swiftc -O -target x86_64-apple-macos13 app/main.swift -framework AppKit -o build/x86_64 2>&1 \
  | grep -v "libswiftCompatibilityPacks" || true
test -f build/x86_64
lipo -create build/arm64 build/x86_64 -output "$APP/Contents/MacOS/WAV Joiner"
rm -rf build
cp app/Info.plist "$APP/Contents/Info.plist"
cp wav_joiner.py app/AppIcon.icns "$APP/Contents/Resources/"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Built: $(pwd)/$APP"
