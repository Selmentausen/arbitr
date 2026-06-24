#!/bin/sh

for i in 1 2 3 4 5; do
    echo "Attempt $i: Installing Playwright Chromium..."
    if playwright install chromium; then
        echo "Browser installed successfully"
        exit 0
    fi
    echo "Attempt $i failed. Waiting 15 seconds..."
    sleep 15
done

echo "WARNING: Could not install Chromium after 5 attempts. Worker may fail to scrape."
exit 0  
