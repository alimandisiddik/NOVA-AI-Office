#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PLIST_NAME="com.nova.ai.office.plist"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

action="${1:-help}"

function check_status() {
    if launchctl list | grep -q "com.nova.ai.office"; then
        echo "NOVA AI Office is currently loaded in launchd."
        # PID could be empty if not running
        local pid
        pid=$(launchctl list com.nova.ai.office | grep '"PID"' | awk '{print $3}' | tr -d '";')
        if [[ -n "${pid}" ]]; then
            echo "Status: RUNNING (PID: ${pid})"
        else
            echo "Status: STOPPED (but loaded)"
        fi
    else
        echo "NOVA AI Office is NOT loaded in launchd."
    fi
}

case "${action}" in
    "start")
        if [[ ! -f "${PLIST_PATH}" ]]; then
            echo "Error: plist not installed. Run '${0} install' first." >&2
            exit 1
        fi
        echo "Starting NOVA AI Office via launchctl..."
        launchctl start com.nova.ai.office
        ;;
    "stop")
        echo "Stopping NOVA AI Office via launchctl..."
        # launchctl stop sends SIGTERM, giving python-telegram-bot time to exit gracefully
        launchctl stop com.nova.ai.office || true
        ;;
    "restart")
        echo "Restarting NOVA AI Office..."
        $0 stop
        sleep 2
        $0 start
        ;;
    "status")
        check_status
        ;;
    "health")
        # Ensure we are running. A simple check is to verify if process is running via PID.
        echo "Checking health..."
        pid=$(launchctl list com.nova.ai.office 2>/dev/null | grep '"PID"' | awk '{print $3}' | tr -d '";')
        if [[ -n "${pid}" ]]; then
            echo "Service is UP (PID: ${pid})."
            exit 0
        else
            echo "Service is DOWN."
            exit 1
        fi
        ;;
    "install")
        echo "Installing launchd plist..."
        mkdir -p "${HOME}/Library/LaunchAgents"
        # Generate the plist dynamically to set correct paths
        cat <<PLIST > "${PLIST_PATH}"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nova.ai.office</string>

    <key>ProgramArguments</key>
    <array>
        <string>${REPOSITORY_ROOT}/.venv/bin/python</string>
        <string>-m</string>
        <string>app.run_singleton</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPOSITORY_ROOT}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <!-- Restart if the process exits with a non-zero status (unexpected failure) -->
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <!-- StandardOutPath and StandardErrorPath are not needed because Python logs to data/nova.log -->
    <key>StandardErrorPath</key>
    <string>/dev/null</string>
    <key>StandardOutPath</key>
    <string>/dev/null</string>
</dict>
</plist>
PLIST
        echo "Loading com.nova.ai.office into launchd..."
        launchctl load -w "${PLIST_PATH}"
        echo "Installation complete."
        ;;
    "uninstall")
        echo "Uninstalling launchd plist..."
        if launchctl list | grep -q "com.nova.ai.office"; then
            launchctl unload -w "${PLIST_PATH}"
        fi
        rm -f "${PLIST_PATH}"
        echo "Uninstallation complete."
        ;;
    "help"|*)
        echo "Usage: ${0} {start|stop|restart|status|health|install|uninstall}"
        exit 1
        ;;
esac
