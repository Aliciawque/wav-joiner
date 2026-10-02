#!/bin/bash
# Builds "WAV Joiner.app" from the files in this folder (needs the Command Line Tools).
set -e
cd "$(dirname "$0")"
APP="WAV Joiner.app"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Apple's Command Line Tools first. Re-run this script when that finishes."
  xcode-select --install
  exit 1
fi
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O app/main.swift -framework AppKit -o "$APP/Contents/MacOS/WAV Joiner"
cp app/Info.plist "$APP/Contents/Info.plist"
cp wav_joiner.py app/AppIcon.icns "$APP/Contents/Resources/"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Built: $(pwd)/$APP"
