#!/bin/bash
# Runs on macOS wake from sleep (via sleepwatcher LaunchAgent).
# Waits for network then force-restarts the OpenClaw gateway to clear
# Node.js event-loop starvation that accumulates during sleep.

for i in $(seq 1 30); do
    ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1 && break
    sleep 2
done

launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
