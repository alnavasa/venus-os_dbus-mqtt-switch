#!/bin/bash
# dbus-mqtt-switch — uninstall.sh
# Stops and removes all dbus-mqtt-switch services.

DRIVER_PATH="/data/etc/dbus-mqtt-switch"

echo "Stopping and removing dbus-mqtt-switch services..."

# Stop and remove service symlinks
for svc_link in /service/dbus-mqtt-switch-*; do
    [ -L "$svc_link" ] || continue
    echo "  Stopping: $svc_link"
    svc -d "$svc_link" 2>/dev/null || true
done

sleep 1

for svc_link in /service/dbus-mqtt-switch-*; do
    [ -L "$svc_link" ] || continue
    rm -f "$svc_link"
    echo "  Removed:  $svc_link"
done

# Clean up generated service directories
for svc_dir in "$DRIVER_PATH"/service-*/; do
    [ -d "$svc_dir" ] || continue
    rm -rf "$svc_dir"
    echo "  Cleaned:  $svc_dir"
done

# Remove rc.local entry
RC_LOCAL="/data/rc.local"
if [ -f "$RC_LOCAL" ] && grep -q "dbus-mqtt-switch" "$RC_LOCAL"; then
    sed -i '/dbus-mqtt-switch/d' "$RC_LOCAL"
    echo "  Removed persistence from $RC_LOCAL"
fi

echo ""
echo "Done. Driver files remain in $DRIVER_PATH"
echo "Remove manually with: rm -rf $DRIVER_PATH"
