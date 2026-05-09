#!/usr/bin/env bash
# One-shot installer for a Raspberry Pi 4 running Raspberry Pi OS (Bookworm).
# Idempotent — safe to re-run.
#
# Flags:
#   --kiosk   Configure the Pi so the TFT is owned exclusively by Spectre:
#             boot-to-console (no desktop), disable fbtft/fbcp framebuffer
#             drivers, and keep SSH on for remote management.  Use this on
#             deployed field units; skip it on a dev Pi where you still want
#             the normal desktop.

set -euo pipefail

KIOSK=0
for arg in "$@"; do
    case "$arg" in
        --kiosk) KIOSK=1 ;;
        -h|--help)
            grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/spectre}"

# Prefer /boot/firmware/config.txt on Bookworm; fall back to /boot/config.txt.
CONFIG="/boot/firmware/config.txt"
[[ -f $CONFIG ]] || CONFIG="/boot/config.txt"

echo ">> installing system packages"
sudo apt-get update
# Core dependencies.  Modern numpy/Pillow wheels ship their own BLAS, so we
# no longer need libatlas-base-dev (dropped from Debian Trixie anyway).
# We use `arecord` (alsa-utils) for capture and write directly to /dev/fb1
# (kernel framebuffer), so we deliberately do NOT install libportaudio2.
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    fonts-dejavu-core \
    alsa-utils \
    python3-rpi.gpio python3-spidev

# Spectre is a tactical kiosk — it expects exclusive access to the USB
# capture device.  pipewire / pulseaudio (which Pi OS Trixie auto-launches
# under the desktop session) hold the device open and cause arecord to fail
# with "Device or resource busy".  We disable them entirely so the radio mic
# is always free.
echo ">> masking desktop audio servers (pipewire / pulseaudio) on this device"
REAL_USER="${SUDO_USER:-$USER}"
sudo -u "$REAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$REAL_USER")" \
    systemctl --user stop pipewire pipewire-pulse wireplumber pulseaudio.socket pulseaudio 2>/dev/null || true
sudo -u "$REAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$REAL_USER")" \
    systemctl --user disable pipewire pipewire-pulse wireplumber pulseaudio.socket pulseaudio 2>/dev/null || true
sudo -u "$REAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$REAL_USER")" \
    systemctl --user mask pipewire pipewire-pulse wireplumber pulseaudio.socket pulseaudio 2>/dev/null || true
sudo systemctl mask pipewire pipewire-pulse wireplumber pulseaudio.socket pulseaudio 2>/dev/null || true
sudo pkill -9 -f pipewire 2>/dev/null || true
sudo pkill -9 -f pulseaudio 2>/dev/null || true
sudo pkill -9 -f wireplumber 2>/dev/null || true

echo ">> enabling SPI interface"
if ! grep -q "^dtparam=spi=on" "$CONFIG" 2>/dev/null; then
    echo "dtparam=spi=on" | sudo tee -a "$CONFIG" >/dev/null
    echo "   (SPI enabled in $CONFIG — reboot before first run)"
fi

# Make sure the user we'll run as can reach /dev/fb1, /dev/snd/*, /dev/spidev*.
# `video` is the gate for the fbtft kernel framebuffer.
echo ">> ensuring '${SUDO_USER:-$USER}' is in audio/video/spi/gpio groups"
for grp in audio video spi gpio; do
    if ! id -nG "${SUDO_USER:-$USER}" | tr ' ' '\n' | grep -qx "$grp"; then
        sudo usermod -aG "$grp" "${SUDO_USER:-$USER}" || true
        echo "   - added to $grp (re-login or reboot for it to take effect)"
    fi
done

# The installer may be invoked directly OR via sudo; either way, figure out
# who the "real" operator is so we can own files + run the service as them.
REAL_USER="${SUDO_USER:-$USER}"
REAL_GROUP="$(id -gn "$REAL_USER")"

echo ">> creating $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo chown "$REAL_USER:$REAL_GROUP" "$INSTALL_DIR"
rsync -a --delete --exclude='.venv' --exclude='.git' "$REPO_ROOT"/ "$INSTALL_DIR"/

echo ">> creating virtualenv"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

echo ">> copying .env.example (if no .env yet)"
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "   !! edit $INSTALL_DIR/.env and set ELEVENLABS_API_KEY before starting"
fi

echo ">> installing systemd unit and enabling auto-start on boot"
# Substitute the current user/group into the unit so the service runs as the
# person who installed it — not the hard-coded "pi" from the template.  That
# way the installer works on Pi OS (user=pi) and on custom images (user=
# lilcipher, ubuntu, admin, …) without manual fix-ups.
echo "   running as user=$REAL_USER group=$REAL_GROUP"
sudo install -m 0644 "$REPO_ROOT/systemd/spectre.service" /etc/systemd/system/spectre.service
sudo sed -i "s/^User=.*/User=$REAL_USER/" /etc/systemd/system/spectre.service
sudo sed -i "s/^Group=.*/Group=$REAL_GROUP/" /etc/systemd/system/spectre.service
sudo systemctl daemon-reload
sudo systemctl enable spectre.service
echo "   service will start automatically on every boot."

if [[ $KIOSK -eq 1 ]]; then
    echo ""
    echo ">> configuring kiosk mode (TFT owned exclusively by Spectre)"

    # 1. Boot to console — no LightDM, no X autostart.
    echo "   - boot target: multi-user (console)"
    sudo systemctl set-default multi-user.target >/dev/null
    for dm in lightdm gdm gdm3 sddm; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^${dm}.service"; then
            echo "   - disabling display manager: $dm"
            sudo systemctl disable --now "$dm" 2>/dev/null || true
        fi
    done

    # 2. Shut down any fbcp-style HDMI→TFT mirror service.
    for svc in fbcp fbcp-ili9341 rpi-fbcp; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}.service"; then
            echo "   - disabling framebuffer mirror: $svc"
            sudo systemctl disable --now "$svc" 2>/dev/null || true
        fi
    done
    if pgrep -x fbcp >/dev/null 2>&1; then
        echo "   - killing running fbcp process"
        sudo pkill -x fbcp || true
    fi

    # 3. The default Spectre display backend is "fb1" — it paints directly
    #    into the kernel framebuffer that fbtft creates from the dtoverlay.
    #    So unlike the old luma.lcd path, we now WANT the fbtft overlay to be
    #    enabled.  We don't add one ourselves (the operator wires their own
    #    panel and knows the GPIO pins), but we warn if there isn't one.
    if ! grep -Eq '^dtoverlay=(fbtft|tft|ili9341|rpi-display|waveshare)' "$CONFIG"; then
        echo "   - WARN: no fbtft/ili9341 dtoverlay found in $CONFIG."
        echo "     Spectre's default backend (fb1) needs a kernel framebuffer."
        echo "     Add a line like:"
        echo "       dtoverlay=fbtft,spi0-0,ili9341,bgr,reset_pin=25,dc_pin=24,led_pin=18,rotate=90,speed=32000000"
        echo "     ...and reboot before starting spectre.service."
    fi

    # 4. Leave SSH on for remote management.  On lite / custom images the
    #    ssh server may not be installed at all — pull it in so kiosk mode
    #    doesn't leave the Pi headless and unreachable.
    if ! dpkg -s openssh-server >/dev/null 2>&1; then
        echo "   - installing openssh-server"
        sudo apt-get install -y openssh-server >/dev/null
    fi
    if systemctl list-unit-files 2>/dev/null | grep -q '^ssh.service'; then
        sudo systemctl enable --now ssh 2>/dev/null || true
        echo "   - SSH enabled (manage remotely with: ssh $REAL_USER@<host>)"
    fi

    echo "   kiosk mode applied.  REBOOT the Pi for the changes to take full effect."
fi

# Only start immediately if the user has already filled in the API key.
if grep -q "^ELEVENLABS_API_KEY=..*[A-Za-z0-9]" "$INSTALL_DIR/.env" 2>/dev/null; then
    echo ">> starting spectre.service now"
    sudo systemctl restart spectre.service
    echo "   tail the logs with:  journalctl -fu spectre"
else
    echo ""
    echo "   !! Edit $INSTALL_DIR/.env and set ELEVENLABS_API_KEY, then run:"
    echo "         sudo systemctl start spectre"
    echo "   The service is already enabled, so it will auto-start on the next boot."
fi

echo ">> done"
