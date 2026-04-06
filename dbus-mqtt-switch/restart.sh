#!/bin/bash
# dbus-mqtt-switch — restart.sh
# Restarts all active dbus-mqtt-switch services.

echo "Restarting dbus-mqtt-switch services..."

found=0
for svc_link in /service/dbus-mqtt-switch-*; do
    [ -L "$svc_link" ] || continue
    svc -t "$svc_link"
    found=$((found + 1))
done

if [ "$found" -eq 0 ]; then
    echo "No active services found. Run install.sh first."
    exit 1
fi

sleep 1

for svc_link in /service/dbus-mqtt-switch-*; do
    [ -L "$svc_link" ] || continue
    svstat "$svc_link"
done
