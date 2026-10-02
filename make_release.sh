#!/bin/bash
# Builds the app and packages it as dist/WAV-Joiner-<version>-macOS.zip for a GitHub Release.
set -e
cd "$(dirname "$0")"
bash build_app.sh
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" app/Info.plist)
OUT="dist/WAV-Joiner-$VERSION-macOS.zip"
mkdir -p dist
rm -f "$OUT"
ditto -c -k --sequesterRsrc --keepParent "WAV Joiner.app" "$OUT"
echo "Architectures: $(lipo -archs "WAV Joiner.app/Contents/MacOS/WAV Joiner")"
echo "Packaged: $(pwd)/$OUT"
