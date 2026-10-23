#!/usr/bin/env bash
set -e

echo "=== Installing Playwright System Dependencies and Browsers ==="
python3 -m pip install --upgrade pip
python3 -m pip install playwright
python3 -m playwright install --with-deps chromium firefox
echo "=== Playwright Installation Complete ==="
