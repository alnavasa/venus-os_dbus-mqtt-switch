#!/bin/bash
# dbus-mqtt-switch — install.sh
# Scans for config-*.ini files and creates a daemontools service for each.
# Also sets up /data/rc.local for persistence across Venus OS firmware updates.

DRIVER_PATH="/data/etc/dbus-mqtt-switch"

echo "Installing dbus-mqtt-switch..."
echo ""

# ── Check for legacy config.ini ──────────────────────────────────────────────
if [ -f "$DRIVER_PATH/config.ini" ]; then
    echo "WARNING: Found config.ini (legacy naming)."
    echo "  Rename it to config-{name}.ini, e.g.:"
    echo "    mv config.ini config-light1.ini"
    echo ""
fi

# ── Find config files ────────────────────────────────────────────────────────
configs=("$DRIVER_PATH"/config-*.ini)
if [ ! -f "${configs[0]}" ]; then
    echo "ERROR: No config-*.ini files found in $DRIVER_PATH"
    echo "  Copy config.sample.ini to config-{name}.ini and edit it."
    echo "  Example: cp config.sample.ini config-light1.ini"
    exit 1
fi

# ── Set permissions on driver files ──────────────────────────────────────────
chmod 755 "$DRIVER_PATH/dbus-mqtt-switch.py"
chmod 755 "$DRIVER_PATH/install.sh"
chmod 755 "$DRIVER_PATH/restart.sh"
chmod 755 "$DRIVER_PATH/uninstall.sh"

# ── Phase 1: Create service directories and symlinks ─────────────────────────
names=()
for cfg in "${configs[@]}"; do
    name=$(basename "$cfg" .ini)
    name=${name#config-}
    names+=("$name")

    echo "── Instance: $name ──"

    # Create service directory structure
    svc_dir="$DRIVER_PATH/service-$name"
    mkdir -p "$svc_dir/log"

    # Generate run script
    cat > "$svc_dir/run" << EOF
#!/bin/sh
exec $DRIVER_PATH/dbus-mqtt-switch.py config-$name.ini 2>&1
EOF

    # Generate log/run script
    cat > "$svc_dir/log/run" << EOF
#!/bin/sh
exec multilog t s25000 n4 /var/log/dbus-mqtt-switch-$name
EOF

    chmod 755 "$svc_dir/run"
    chmod 755 "$svc_dir/log/run"

    # Create symlink in /service/
    svc_link="/service/dbus-mqtt-switch-$name"
    if [ ! -L "$svc_link" ]; then
        ln -s "$svc_dir" "$svc_link"
        echo "  Created: $svc_link"
    else
        echo "  Exists:  $svc_link"
    fi
done

# ── Phase 2: Wait for daemontools, then start services ───────────────────────
echo ""
echo "Waiting for daemontools to detect services..."
sleep 5

for name in "${names[@]}"; do
    svc -t "/service/dbus-mqtt-switch-$name" 2>/dev/null || true
done

sleep 2

echo ""
for name in "${names[@]}"; do
    svstat "/service/dbus-mqtt-switch-$name"
done

# ── Persistence: add to /data/rc.local ───────────────────────────────────────
RC_LOCAL="/data/rc.local"

if [ -f "$RC_LOCAL" ] && grep -q "dbus-mqtt-switch" "$RC_LOCAL"; then
    echo ""
    echo "Persistence: already in $RC_LOCAL"
else
    [ -f "$RC_LOCAL" ] || echo '#!/bin/bash' > "$RC_LOCAL"
    chmod 755 "$RC_LOCAL"

    cat >> "$RC_LOCAL" << 'RCEOF'

# dbus-mqtt-switch — recreate service symlinks after firmware update
if [ -d /data/etc/dbus-mqtt-switch ]; then
    bash /data/etc/dbus-mqtt-switch/install.sh > /dev/null 2>&1 &
fi
RCEOF
    echo ""
    echo "Persistence: added to $RC_LOCAL"
fi

echo ""
echo "Done. Check logs with:"
for name in "${names[@]}"; do
    echo "  tail -f /var/log/dbus-mqtt-switch-$name/current"
done
