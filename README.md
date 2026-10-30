# SurvCam

A self-hosted, multi-camera CCTV system built from cheap ESP32-CAM modules, an old PC, and a native Android app. No subscription, no cloud storage, and no port forwarding.

- **Cameras:** ESP32-CAM boards on Wi-Fi, wired Ethernet (W5500), or a two-board "bridge" setup. They stream MJPEG and send MQTT heartbeats.
- **Server:** FastAPI + SQLite + OpenCV on Ubuntu Server. It records, detects motion, monitors camera health, and sends push notifications.
- **App:** Native Android app (Kotlin) for live multi-camera view, recording playback, snapshots, camera control, and notifications.
- **Remote access:** Cloudflare Tunnel publishes the API over HTTPS even behind CGNAT.

The 12-camera build is deployed and runs on a refurbished dual-core Intel i3 PC, with hardware cost under ₹25,000 (approximately $260). Commercial systems of this size cost ₹50,000+ (approximately $530+).

## Contents

1. [Features](#features)
2. [How it fits together](#how-it-fits-together)
3. [What you need](#what-you-need)
4. [Step 1 - Prepare the server](#step-1---prepare-the-server)
5. [Step 2 - Configure and start the backend](#step-2---configure-and-start-the-backend)
6. [Step 3 - Hardware video encoding (VAAPI)](#step-3---hardware-video-encoding-vaapi)
7. [Step 4 - MQTT broker and firewall](#step-4---mqtt-broker-and-firewall)
8. [Step 5 - Storage, retention and schedule](#step-5---storage-retention-and-schedule)
9. [Step 6 - Build and flash the camera firmware](#step-6---build-and-flash-the-camera-firmware)
10. [Step 7 - Register cameras on the server](#step-7---register-cameras-on-the-server)
11. [Step 8 - Build the Android app](#step-8---build-the-android-app)
12. [Step 9 - Remote access with Cloudflare Tunnel](#step-9---remote-access-with-cloudflare-tunnel)
13. [Step 10 - Self-healing (recommended)](#step-10---self-healing-recommended)
14. [Command reference](#command-reference)
15. [Troubleshooting](#troubleshooting)
16. [Security notes](#security-notes)
17. [Known limitations](#known-limitations)
18. [Contributing and license](#contributing-and-license)

## Features

- Live view of many cameras at once (multi-view grid in the app). Cameras serve one stream each, and the server fans it out to any number of viewers.
- 24/7 recording during configurable hours, motion-triggered recording otherwise, with cross-camera triggering.
- Hardware H.264 encoding through Intel VAAPI, with an automatic software (libx264) fallback.
- Playback by day and hour, plus snapshot browsing.
- JWT authentication with a mandatory Telegram two-factor code at login.
- MQTT heartbeats with Last Will detection, plus independent TCP reachability probes, to tell camera, network, and broker failures apart.
- Push notifications (optional, via Firebase Cloud Messaging) for offline cameras, recoveries, and after-hours motion.
- Remote firmware updates over MQTT using signed commands, with automatic rollback if the new firmware does not become healthy.
- Disk management: age-based cleanup plus a free-space floor.

## How it fits together

See the [architecture diagram](architecture.md) for the service and network paths. GitHub renders the Mermaid diagram directly.

- Cameras always talk to the server's **LAN IP** for MQTT and firmware downloads. Never point camera MQTT at the public hostname.
- Cloudflare is only for the app and browser connection.

| Port | Where | Purpose | Expose to internet? |
|------|-------|---------|--------------------|
| 8000 | server | FastAPI (published through the tunnel) | Only through Cloudflare Tunnel |
| 1883 | server | Mosquitto MQTT | **Never** |
| 81 | camera | MJPEG stream (`/stream`) | **Never** |
| 8080 | camera | Setup/update page (`/identity`, `/update`), opened by a button press | **Never** |

## What you need

### Hardware

| Item | Notes |
|------|-------|
| Server PC | Any x86-64 PC that can run Ubuntu Server. A dual-core Intel i3 handled 12 cameras. An Intel CPU with Quick Sync is strongly recommended for hardware encoding. Add a second disk or a USB drive for recordings. |
| ESP32-CAM (AI Thinker) x N | One per camera. |
| USB to UART adapter (3.3V Logic) | For the first flash. |
| Power per camera | Stable 5 V, 2 A supply with a short cable and a 1000 µF capacitor on the power rails. Weak power is the most common cause of Wi-Fi dropouts and reboots. |
| W5500 SPI Ethernet module | Only for wired cameras. **3.3 V or 5 V (depending on the version)** |
| ESP32 dev board | Only for the optional bridge setup. |
| Momentary button | Optional but recommended for each camera. It opens the setup/update page. |
| Router/APs | For many cameras, spread them over more than one access point. |

### Software and accounts

- Ubuntu Server LTS on the server, the command-line Android build tools (JDK 17, Node.js, Gradle 8.7, Android SDK command-line tools) for the app, and Arduino IDE 2 for the firmware. Android Studio is not needed.
- A **Telegram bot** for login codes (required).
- A **domain name** and a free Cloudflare account, only if you want access away from your network. Tailscale or a VPN works as an alternative.
- A Firebase project for push notifications (optional).

## Step 1 - Prepare the server

1. Download Ubuntu Server LTS from [ubuntu.com/download/server](https://ubuntu.com/download/server) and write it to a USB drive with Rufus (Windows) or balenaEtcher. Writing the ISO erases the USB drive. Follow Ubuntu's [installation tutorial](https://ubuntu.com/tutorials/install-ubuntu-server).
2. In the installer, create a non-root user, enable OpenSSH, and use wired Ethernet if possible.
3. After the first boot, install the packages:

   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y git python3-venv python3-pip python3-dev build-essential \
       ffmpeg mosquitto mosquitto-clients sqlite3 jq curl vainfo sysstat
   ```

4. Give the server a fixed LAN address, ideally a DHCP reservation in your router. Find it with `hostname -I`. This guide calls it `YOUR_SERVER_LAN_IP`.

## Step 2 - Configure and start the backend

```bash
git clone https://github.com/DevarshiBohra/SurvCam-Public.git SurvCam
cd SurvCam
cp .env.example .env
nano .env
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` pins `bcrypt` to a 4.x release (4.0.1 is known to work) next to `passlib`. The `passlib[bcrypt]` extra alone installs the newest bcrypt, and bcrypt 5.x breaks `passlib`: `create_admin.py` then fails with "password cannot be longer than 72 bytes". Do not remove that pin.

Edit `.env` and replace every `REPLACE_...`, `YOUR_...`, or blank value that applies:

| Variable | What to put |
|----------|-------------|
| `SURVCAM_JWT_SECRET` | `openssl rand -hex 32`. Never reuse the OTA key. |
| `SURVCAM_OTA_KEY` | A different value from `openssl rand -hex 24`. You enter this same key in the firmware and on each camera. |
| `SURVCAM_FIRMWARE_BASE_URL` | `http://YOUR_SERVER_LAN_IP:8000` (cameras download updates from this LAN address). |
| `SURVCAM_CONTROL_USERS` | Comma-separated usernames allowed to upload firmware and reboot, update, or roll back cameras. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | See below. Login does not work without them. |
| `SURVCAM_FIREBASE_CREDENTIALS` | Optional. Path to your own Firebase service-account JSON. |
| Storage variables | See [Step 5](#step-5---storage-retention-and-schedule). |

The backend loads the root `.env` automatically.

### Telegram bot for two-factor login

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the bot token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates` in a browser. The `chat` → `id` number in the reply is your `TELEGRAM_CHAT_ID`.

### Create the administrator and test

```bash
python create_admin.py        # prompts for a username and a password of 12+ characters
uvicorn main:app --host 0.0.0.0 --port 8000
```

From another computer on your LAN, open `http://YOUR_SERVER_LAN_IP:8000/health`. Press Ctrl+C when it responds.

### Run it as a service

Create `/etc/systemd/system/survcam-backend.service`. Replace `YOUR_UBUNTU_USER` and the paths with yours:

```ini
[Unit]
Description=SurvCam API server
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
User=YOUR_UBUNTU_USER
WorkingDirectory=/home/YOUR_UBUNTU_USER/SurvCam/backend
EnvironmentFile=/home/YOUR_UBUNTU_USER/SurvCam/.env
ExecStart=/home/YOUR_UBUNTU_USER/SurvCam/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now survcam-backend
sudo systemctl status survcam-backend
sudo journalctl -u survcam-backend -f
```

`TimeoutStopSec=15` stops a restart from waiting a long time for open live streams to close.

## Step 3 - Hardware video encoding (VAAPI)

Recording uses ffmpeg with `h264_vaapi` on `/dev/dri/renderD128`, and **falls back to software libx264 automatically** if hardware encoding fails to start. Hardware encoding is what lets a dual-core i3 record 12+ cameras: per-camera CPU load dropped from about 30% to about 10% in my build.

1. Check that the GPU device exists: `ls /dev/dri/`.
2. Install the VAAPI driver for your CPU generation:

   | CPU generation | Package |
   |----------------|---------|
   | Older Intel (Sandy Bridge to Haswell, e.g. 2nd-gen Core) | `i965-va-driver` |
   | Broadwell and newer | `intel-media-va-driver` (or `intel-media-va-driver-non-free`) |

3. Verify with `vainfo`. It should list `VAProfileH264...` entries with `VAEntrypointEncSlice`.
4. Run the user that starts the backend in the `render` and `video` groups: `sudo usermod -aG render,video YOUR_UBUNTU_USER`, then log out and back in and restart the service.
5. Monitor the GPU with `sudo intel_gpu_top` (from `intel-gpu-tools`, supported GPUs only) and the CPU with `top` or `iostat -xz 1`.

Tuning constants (`USE_VAAPI`, `VAAPI_DEVICE`, `VAAPI_BITRATE`, `VAAPI_MAXRATE`, `VAAPI_BUFSIZE`, `SEGMENT_SECONDS`) are at the top of `backend/recorder.py`. Set `USE_VAAPI = False` to force software encoding.

## Step 4 - MQTT broker and firewall

The firmware connects to Mosquitto on TCP 1883. For a LAN-only broker, create `/etc/mosquitto/conf.d/survcam.conf`:

```conf
listener 1883 0.0.0.0
allow_anonymous true
```

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto
```

**The camera firmware does not support MQTT usernames or passwords yet**, so this setup relies on LAN isolation and the firewall. Anyone on your LAN can publish fake heartbeats. Firmware commands (update, reboot, rollback) are signed with the OTA key and are not affected. Keep the broker off the internet.

If you use UFW, replace `YOUR_LAN_CIDR` with your subnet (for example `192.168.1.0/24`):

```bash
sudo ufw allow OpenSSH
sudo ufw allow from YOUR_LAN_CIDR to any port 1883 proto tcp
sudo ufw allow from YOUR_LAN_CIDR to any port 8000 proto tcp
sudo ufw enable
sudo ufw status
```

## Step 5 - Storage, retention and schedule

**Paths.** By default everything is stored under `backend/data`. For an external recordings disk, mount it (use a UUID in `/etc/fstab` so the mount survives reboots) and set these in `.env`:

```conf
SURVCAM_STORAGE_MOUNT=/mnt/survcam-recordings
SURVCAM_STORAGE_UUID=YOUR_DISK_UUID
SURVCAM_RECORDINGS_ROOT=/mnt/survcam-recordings/recordings
SURVCAM_SNAPSHOT_ROOT=/mnt/survcam-recordings/thumbnails
```

Find the UUID with `lsblk -f`. When a UUID is set, the backend refuses to record to the wrong disk if the drive is missing.

**Retention.** `backend/auto_recorder.py` deletes recordings older than `RETENTION_DAYS` (default 9) and keeps `MIN_FREE_BYTES` (default 30 GB) free by deleting the oldest recordings first.

**Schedule.** Defaults are:

- Always-on recording from 09:00 to 20:00, Monday to Saturday.
- Motion-triggered recording at other times, and all day Sunday.

These hours are **constants, not environment variables**. To change them, edit `OFFICE_OPEN` and `OFFICE_CLOSE` in **both** `backend/recording_scheduler.py` and `backend/motion_detector.py`, and the weekday rule in the same files (Sunday is `weekday() == 6`).

**Motion tuning (optional).** `SURVCAM_PER_CAMERA_MIN_CHANGED_PIXELS` and `SURVCAM_MOTION_BOTTOM_HALF_CAMERA_IDS` in `.env` tune the detector per camera. `SURVCAM_IGNORED_CAMERA_NOTIFICATION_IDS` silences notifications for chosen cameras.

## Step 6 - Build and flash the camera firmware

### Toolchain

1. Install [Arduino IDE 2](https://docs.arduino.cc/software/ide-v2/tutorials/getting-started/ide-v2-downloading-and-installing).
2. In **File > Preferences**, add `https://espressif.github.io/arduino-esp32/package_esp32_index.json` to the board manager URLs.
3. In **Boards Manager**, install **esp32 by Espressif Systems**, **version 3.0 or newer** (the author builds with 3.3.11). The sketches use the ESP-IDF 5 watchdog API (`esp_task_wdt_config_t`) and will not compile on core 2.x.
4. In **Library Manager**, install **WiFiManager** (tzapu) and **PubSubClient** (Nick O'Leary). For the Ethernet sketch, also install [EthernetSPI2](https://github.com/arhi/EthernetSPI2). Use the latest stable releases and open an issue if a new release breaks the build.

### Choose a sketch

| Sketch folder | Use it for |
|---------------|-----------|
| `esp32/esp32cam_ota_0.11.0-wifi/` | AI Thinker ESP32-CAM on Wi-Fi |
| `esp32/esp32cam_ota_0.11.0-lan/` | AI Thinker ESP32-CAM with a W5500 Ethernet module |
| `esp32/esp32cam_ota_0.11.0-bridge-cameraNode/` | Two boards: use when a camera board's own Wi-Fi is bad or damaged. The `camera_node` board captures frames and sends them over a UART to the `bridge` board, which handles Wi-Fi, MQTT, and updates. |

Each sketch must stay in its own folder. The bridge and camera-node sketches need the `survlink.h` beside them.

### Edit the values

Open the sketch and set:

- `MQTT_HOST`: your server's **LAN IP** (`YOUR_SERVER_LAN_IP`), not the public hostname.
- `OTA_KEY`: the same value as `SURVCAM_OTA_KEY` in `.env`. While it is still `CHANGE-ME-to-a-long-random-key`, updates stay disabled.
- Leave `DEFAULT_CAMERA_ID`, `DEFAULT_CAMERA_NAME`, and `DEFAULT_CAMERA_PASSWORD` empty. The camera's identity is stored in its flash through a setup page, so **one compiled `.bin` works for every camera** and survives updates.

### Wiring for the Ethernet (W5500) build

| ESP32-CAM | W5500 |
|-----------|-----------|
| GPIO12 | MISO |
| GPIO13 | MOSI |
| GPIO14 | SCLK |
| GPIO2 | SCS (CS) |
| GND | GND |
| 3.3 V / 5V (Depending upon the W5500 module) | VCC |

Optional button: GPIO15 to GND.

### Wiring for the bridge build

| Camera board | Bridge board |
|--------------|--------------|
| GPIO14 (TX) | GPIO16 (RX2) |
| GPIO13 (RX) | GPIO17 (TX2) |
| GND | GND |

Use wires shorter than about 15 cm, twist the ground wire with them, and power both boards from the same 5 V supply. The link speed is 2,000,000 baud in both sketches and must match. Lower it if wires are long. Flash `camera_node` by cable with the bridge disconnected. Select **ESP32 Dev Module** for the bridge.

### First flash (by cable)

1. Select **Tools > Board > ESP32 Arduino > AI Thinker ESP32-CAM** (or **ESP32 Dev Module** for the bridge).
2. Select the partition scheme **Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)**. The OTA and rollback features need it.
3. Connect the adapter: TX → U0R, RX → U0T, GND → GND, and a good 5 V supply. Hold GPIO0 to GND, reset, upload, remove the jumper, and reset again. A weak USB port often causes brownouts and upload failures.
4. The first flash of every camera, including Ethernet and bridge cameras, must be done by cable. After that, updates go over the network.

### Give each camera its identity

1. **Wi-Fi cameras:** On first boot the camera opens a Wi-Fi access point called `ESP32CAM-setup` for five minutes. Join it from a phone and enter your Wi-Fi details. You can reopen this portal later by holding the button for five seconds (button between GPIO13 and GND, using the internal pull-up).
2. Open the serial monitor at 115200 baud. It prints the camera's IP and the line `NO CAMERA ID STORED`.
3. Open `http://CAMERA_IP:8080/identity`. For a configured camera, press the button twice quickly to open the page for 10 minutes.
4. Enter the **OTA key**, a **camera ID** such as `cam001`, a **name**, and a **password**, then press **Save identity and restart**.
5. Register the same ID and password on the server in [Step 7](#step-7---register-cameras-on-the-server). The server ignores heartbeats from IDs it does not know.

For Ethernet cameras the process is the same, minus Wi-Fi setup. They use DHCP and print their IP in the serial monitor. A wired camera restarts itself when its network link drops and comes back.

### Install updates later (no cable)

1. In Arduino IDE choose **Sketch > Export Compiled Binary**. Use the `.ino.bin` file from the sketch folder (not the `bootloader` or `merged` files).
2. Upload it to the server and send it to a camera (see the [command reference](#command-reference), or use the app).
3. If the new firmware does not stay healthy for two minutes, or fails to boot three times, the camera rolls back to the previous version.

**Warning:** Wi-Fi, Ethernet, and bridge builds are different. Only send a build to the matching kind of camera, otherwise it can need a cable reflash.

## Step 7 - Register cameras on the server

Cameras only appear after you create them on the server. Run these on the server (details in the [command reference](#command-reference)):

```bash
# 1) Log in. This returns a challenge_id, and the Telegram bot sends you a code.
curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_ADMIN_USERNAME","password":"YOUR_PASSWORD"}'

# 2) Verify the code and keep the token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/verify-2fa \
  -H "Content-Type: application/json" \
  -d '{"challenge_id":"CHALLENGE_ID","code":"TELEGRAM_CODE"}' | jq -r .access_token)

# 3) Create the camera. The ID and password must match what you entered on the camera.
curl -X POST http://127.0.0.1:8000/cameras \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_id":"cam001","name":"Front Gate","password":"YOUR_CAMERA_PASSWORD"}'
```

Within about 5 seconds the camera should show as online in the app. If not, see [Troubleshooting](#troubleshooting).

## Step 8 - Build the Android app

The app is built entirely from the command line. You do not need Android Studio. Do this on any Windows, Linux, or macOS computer.

### 1. Install the tools

| Tool | Version | Notes |
|------|---------|-------|
| JDK | 17 | For example [Eclipse Temurin 17](https://adoptium.net/). Check with `java -version`. |
| Node.js | current LTS | Only runs the small build scripts. Check with `node --version`. |
| Gradle | **8.7** | Install from [gradle.org/install](https://gradle.org/install/). The build scripts call the `gradle` command, so it must be on your `PATH`. Gradle 9.x is not compatible with this project's Android Gradle plugin (8.5.2). Check with `gradle --version`. |
| Android SDK command-line tools | latest | Download "Command line tools only" from [developer.android.com/studio#command-tools](https://developer.android.com/studio#command-tools). |

### 2. Set up the Android SDK

Unzip the command-line tools into a folder of your choice, so the final layout is `ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager`. Then set `ANDROID_HOME` to that folder and install the SDK packages the project needs.

Linux / macOS:

```bash
export ANDROID_HOME="$HOME/android-sdk"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-35" "build-tools;34.0.0"
```

Windows (PowerShell):

```powershell
$env:ANDROID_HOME = "$HOME\android-sdk"
$env:Path += ";$env:ANDROID_HOME\cmdline-tools\latest\bin;$env:ANDROID_HOME\platform-tools"
sdkmanager.bat --licenses
sdkmanager.bat "platform-tools" "platforms;android-35" "build-tools;34.0.0"
```

Add the `ANDROID_HOME` and `PATH` settings to your shell profile (or Windows environment variables) so they persist.

### 3. Check the setup and build

```bash
cd apk/SurvCam_cameracontrol_3
npm run doctor           # checks Node, Java, Gradle and ANDROID_HOME
npm run build:debug      # Windows PowerShell: npm.cmd run build:debug
```

The first build downloads dependencies and can take several minutes. When it finishes, the APK is at:

```
apk/SurvCam_cameracontrol_3/app/build/outputs/apk/debug/app-debug.apk
```

### 4. Install it on your phone

- Over USB (enable USB debugging on the phone): `adb install -r app/build/outputs/apk/debug/app-debug.apk`
- Or copy the APK to the phone and open it. Android will ask you to allow installs from that source.

The debug APK is fine for personal use. For a signed release build, run `npm run build:release` to get an unsigned APK, then sign it:

```bash
keytool -genkeypair -v -keystore survcam-release.jks -alias survcam -keyalg RSA -keysize 2048 -validity 10000
"$ANDROID_HOME/build-tools/34.0.0/zipalign" -v -p 4 app/build/outputs/apk/release/app-release-unsigned.apk survcam-aligned.apk
"$ANDROID_HOME/build-tools/34.0.0/apksigner" sign --ks survcam-release.jks --out survcam-release.apk survcam-aligned.apk
```

Keep the `.jks` file and its passwords private and backed up. You need the same key for every future update.

### 5. First launch

The app ships with **no server address**. On first launch it shows the "Server URL" screen:

1. Enter your server's address:
   - on your home network: `http://YOUR_SERVER_LAN_IP:8000`
   - for remote access (after [Step 9](#step-9---remote-access-with-cloudflare-tunnel)): `https://survcam.YOUR_DOMAIN`
2. Tap **Test Connection**, then **Save and Continue**.
3. Sign in with the administrator account and enter the Telegram code.

The address is stored on the phone, so you enter it once. You can change it later under **Settings**. It is removed if you clear the app's data or uninstall it.

### Optional: push notifications

Notifications need your own Firebase project. Create one, add an Android app whose package name matches `applicationId` in `app/build.gradle` (`org.example.survcam` by default), download `google-services.json`, and put it in `apk/SurvCam_cameracontrol_3/app/` **before building**. Without that file the app builds and works normally, and Firebase stays off. Never commit `google-services.json`.

## Step 9 - Remote access with Cloudflare Tunnel

The server makes an **outbound** connection to Cloudflare, so this works behind CGNAT with no port forwarding and no public IP. You need a domain that you control.

1. Buy a domain from any registrar.
2. Create a free Cloudflare account, **Add a site**, choose the Free plan, and copy the two nameservers Cloudflare shows you.
3. At your registrar, replace the domain's nameservers with those two. Activation can take from minutes to hours.

### Option A: command line (no Zero Trust dashboard, no card)

Locally managed tunnels use only a free Cloudflare account with your domain. The Zero Trust dashboard is not needed, and it is the part that asks for card details.

1. Install `cloudflared` from [Cloudflare's downloads page](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Log in. It prints a link, you pick your domain, and `cert.pem` is saved to `~/.cloudflared/`:

   ```bash
   cloudflared tunnel login
   ```

3. Create the tunnel, then the DNS record that points your hostname at it:

   ```bash
   cloudflared tunnel create survcam
   cloudflared tunnel route dns survcam survcam.YOUR_DOMAIN
   ```

4. Create `/etc/cloudflared/config.yml` (copy the credentials JSON that `create` saved into `/etc/cloudflared/`):

   ```yaml
   tunnel: YOUR_TUNNEL_ID
   credentials-file: /etc/cloudflared/YOUR_TUNNEL_ID.json
   protocol: http2

   ingress:
     - hostname: survcam.YOUR_DOMAIN
       service: http://localhost:8000
     - service: http_status:404
   ```

5. Install and start the service:

   ```bash
   sudo cloudflared service install
   sudo systemctl enable --now cloudflared
   cloudflared tunnel list
   ```

   `cloudflared tunnel list` should show live connections.

`protocol: http2` is the author's setting for a network where QUIC was unreliable. It can be removed.

### Option B: Zero Trust dashboard

In the Cloudflare dashboard, go to **Zero Trust > Networks > Tunnels**, create a tunnel, run the connector command it gives you on the server, and add a **Published application** route to `http://localhost:8000`. Cloudflare may ask for payment details when you first open Zero Trust, even for the free plan.

### Test

From a phone with Wi-Fi turned off, open `https://survcam.YOUR_DOMAIN/health`. Then set the same URL in the app.

**Treat `cert.pem` and the tunnel credentials JSON as secrets.** Anyone with them can run your tunnel or change your DNS. Keep them out of Git and use `chmod 600`. Upgrade `cloudflared` regularly.

## Step 10 - Self-healing (recommended)

A small timer script restarts the backend when `/health` stops responding, and restarts the tunnel when the public URL stops working while the local backend is fine. Save it as `/usr/local/bin/survcam-watchdog.sh` and run `sudo chmod +x /usr/local/bin/survcam-watchdog.sh`:

```bash
#!/usr/bin/env bash
# Restart the backend after 3 failed local health checks, and cloudflared after
# 3 failed public checks (only tested while the local backend is healthy).
set -u
LOCAL_URL="http://127.0.0.1:8000/health"
PUBLIC_URL="https://YOUR_PUBLIC_HOSTNAME/health"
STATE_DIR="/var/lib/survcam-watchdog"
FAILS_BEFORE_RESTART=3
mkdir -p "$STATE_DIR"

check() {   # check <name> <url> <service>
  local name="$1" url="$2" service="$3" n
  if curl -fsS --max-time 10 "$url" >/dev/null 2>&1; then
    echo 0 > "$STATE_DIR/$name"
    return 0
  fi
  n=$(( $(cat "$STATE_DIR/$name" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$STATE_DIR/$name"
  if [ "$n" -ge "$FAILS_BEFORE_RESTART" ]; then
    logger -t survcam-watchdog "$name check failed $n times - restarting $service"
    systemctl restart "$service"
    echo 0 > "$STATE_DIR/$name"
  fi
  return 1
}

if check local "$LOCAL_URL" survcam-backend; then
  check public "$PUBLIC_URL" cloudflared
fi
```

`/etc/systemd/system/survcam-watchdog.service`:

```ini
[Unit]
Description=SurvCam health watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/survcam-watchdog.sh
```

`/etc/systemd/system/survcam-watchdog.timer`:

```ini
[Unit]
Description=Run the SurvCam watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now survcam-watchdog.timer
journalctl -t survcam-watchdog      # shows restarts
```

Also consider these, depending on your hardware:

- **Auto-start after a power cut:** many PCs have a BIOS option "Restore on AC power loss". If yours does not, a UPS or inverter keeps the server running through short outages.
- **Hardware watchdog:** `sudo apt install watchdog` plus the right kernel module for your board can hard-reset a frozen machine. Read your board's documentation and test it, because a wrong configuration can cause reboot loops.
- **Camera side:** the firmware already reboots a camera after prolonged Wi-Fi or MQTT loss and has a task watchdog.

## Command reference

Commands for everyday administration. Run them on the server. Set these once per shell:

```bash
export SURVCAM_DIR="$HOME/SurvCam"
export DB="$SURVCAM_DIR/backend/data/survcam.db"   # or your SURVCAM_DATABASE_PATH
```

Tip: start a command with a space so it stays out of your shell history, since several below contain passwords or tokens.

### Get an access token

```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_USERNAME","password":"YOUR_PASSWORD"}'
```

The reply contains a `challenge_id`, and the Telegram bot sends you a code. If login returns an error about `SURVCAM_JWT_SECRET`, the service did not load `.env`: check the `EnvironmentFile` path in the unit file, then `sudo systemctl restart survcam-backend`.

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/verify-2fa \
  -H "Content-Type: application/json" \
  -d '{"challenge_id":"CHALLENGE_ID","code":"TELEGRAM_CODE"}' | jq -r .access_token)
```

Tokens are valid for `SURVCAM_ACCESS_TOKEN_EXPIRE_DAYS` (30 by default). Treat them like passwords.

### Cameras

```bash
# Add a camera (the ID and password must match what you entered on the camera)
curl -X POST http://127.0.0.1:8000/cameras \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"camera_id":"cam001","name":"Front Gate","password":"YOUR_CAMERA_PASSWORD"}'

# Rename a camera (the API has no rename call, so edit the database directly)
sqlite3 "$DB" "PRAGMA busy_timeout=30000;
  UPDATE cameras SET name='New Camera Name', updated_at=datetime('now') WHERE camera_id='cam001';
  SELECT camera_id, name FROM cameras WHERE camera_id='cam001';"

# Latest Wi-Fi signal strength of one camera, refreshed every 5 seconds
watch -n 5 "sqlite3 $DB \"SELECT recorded_at, rssi FROM heartbeat_history WHERE camera_id='cam001' ORDER BY recorded_at DESC LIMIT 1;\""
```

### Firmware on the server

The user must be listed in `SURVCAM_CONTROL_USERS`.

```bash
# Upload a compiled .bin (use the matching Wi-Fi / Ethernet / bridge build)
curl -X POST "http://127.0.0.1:8000/firmware?version=0.11.0-ota-lan" \
  -H "Authorization: Bearer $TOKEN" \
  --data-binary @/path/to/sketch.ino.bin

# List uploaded firmware
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/firmware

# Send an update, reboot, or rollback command to one camera (it must be online)
curl -X POST http://127.0.0.1:8000/cameras/cam001/ota \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"version":"0.11.0-ota-lan"}'
curl -X POST http://127.0.0.1:8000/cameras/cam001/reboot   -H "Authorization: Bearer $TOKEN"
curl -X POST http://127.0.0.1:8000/cameras/cam001/rollback -H "Authorization: Bearer $TOKEN"

# Delete firmware from the server
curl -X DELETE "http://127.0.0.1:8000/firmware/0.11.0-ota-lan" \
  -H "Authorization: Bearer $TOKEN"
```

Add `&latest=false` to the upload to keep a special build from becoming the default, or `&camera=cam012` for a build tied to one camera. The same actions are available in the app.

### Server status

```bash
sudo systemctl status survcam-backend
sudo journalctl -u survcam-backend -f
df -hT / /mnt/survcam-recordings        # storage on both disks
top                                      # CPU
iostat -xz 1                             # disk activity (Ctrl+C to stop)
sudo intel_gpu_top                       # GPU load (supported Intel GPUs)
```

### Check which devices are muted

```bash
cd "$SURVCAM_DIR/backend" && .venv/bin/python - <<'PY'
import sqlite3, os
from datetime import datetime
db = os.environ.get("SURVCAM_DATABASE_PATH", "data/survcam.db")
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
c.row_factory = sqlite3.Row
now = datetime.now()
rows = c.execute("SELECT token, muted_until FROM device_tokens "
                 "WHERE muted_until IS NOT NULL ORDER BY muted_until DESC").fetchall()
active = [(r, datetime.fromisoformat(r["muted_until"]) - now)
          for r in rows if datetime.fromisoformat(r["muted_until"]) > now]
if not active:
    print("No devices are currently muted.")
for r, left in active:
    print(f"MUTED | token={r['token'][:16]}... | until={r['muted_until']} | remaining={left}")
PY
```

### Send a test notification

```bash
cd "$SURVCAM_DIR/backend" && .venv/bin/python - <<'PY'
from database import init_db
from notifications import init_firebase, send_notification

init_db()
init_firebase()
send_notification(
    notif_type="test",
    title="Test notification",
    body="This is a manual SurvCam test notification.",
    camera_id="manual-test",
    data={"source": "terminal"},
    data_only=False,
)
PY
```

Needs Firebase set up ([Step 8](#step-8---build-the-android-app) and `SURVCAM_FIREBASE_CREDENTIALS`).

### Internet speed test

Paste the function into the terminal, then run `speedtest`:

```bash
speedtest() {
  local download upload latency

  download=$(curl -L --max-time 60 -o /dev/null -sS \
    -w '%{speed_download}' \
    'https://speed.cloudflare.com/__down?bytes=10000000') || return 1

  upload=$(head -c 5000000 /dev/zero | curl -X POST --max-time 60 -sS \
    -o /dev/null -w '%{speed_upload}' \
    --data-binary @- 'https://speed.cloudflare.com/__up') || return 1

  latency=$(curl -L --max-time 10 -o /dev/null -sS \
    -w '%{time_total}' \
    'https://speed.cloudflare.com/__down?bytes=1') || return 1

  awk -v d="$download" -v u="$upload" -v l="$latency" \
    'BEGIN {
      printf "Download: %.2f Mbps\nUpload:   %.2f Mbps\nLatency:  %.0f ms\n",
      d*8/1000000, u*8/1000000, l*1000
    }'
}
```

### Measure real stream frame rate

`backend/fps_check.py` reports the average, slowest, and fastest frames per second of a stream. Run it through the backend, which already holds the camera connection:

```bash
python3 backend/fps_check.py "http://127.0.0.1:8000/live/cam001?access_token=$TOKEN" 60
```

## Troubleshooting

- **Backend does not start or login returns `401`/`500`:** check that `SURVCAM_JWT_SECRET` is a random value of 32+ characters (not the placeholder), that an admin exists (`python create_admin.py`), and that the Telegram variables are set.
- **No Telegram code arrives:** send a message to your bot first, then check `TELEGRAM_CHAT_ID` and `journalctl -u survcam-backend`.
- **A camera never appears in the app:** the camera must be created on the server ([Step 7](#step-7---register-cameras-on-the-server)). Then check Wi-Fi or Ethernet, `MQTT_HOST` in the sketch, TCP 1883 reachability from the camera's network, and Mosquitto. Test with `mosquitto_sub -h 127.0.0.1 -t 'survcam/cameras/+/heartbeat' -v`.
- **Two cameras fight over one IP:** every camera needs a unique MAC. The Ethernet build uses the chip's own factory MAC, so build every camera from the unmodified sketch.
- **Choppy or very slow streams on Wi-Fi:** check power quality first (2 A supply, short cable, 1000 µF capacitor), then access-point load. Spread cameras across more than one AP and measure each camera alone with `fps_check.py`.
- **Cameras that worked for weeks start dropping off Wi-Fi:** check supply voltage under load, antenna and board temperature, and AP placement.
- **Firmware update does nothing:** the OTA key must be the same in `.env` and in the sketch (and at least 16 characters), `SURVCAM_FIRMWARE_BASE_URL` must be a LAN URL the camera can reach, the camera must be online, and the user must be in `SURVCAM_CONTROL_USERS`.
- **Remote access fails (Cloudflare error 530 or a timeout):** check `sudo systemctl status cloudflared` and `cloudflared tunnel list`. Make sure the ingress points to `http://localhost:8000` and test `/health` locally. The watchdog in Step 10 helps with this.
- **Recordings stop:** check free disk space (`df -hT`), that the recordings disk is mounted, and `journalctl -u survcam-backend`.
- **`401` after the secret changes:** all existing tokens become invalid when you change `SURVCAM_JWT_SECRET`. Log in again on every device.

## Security notes

- The API is published to the internet through Cloudflare. Keep authentication on, use strong passwords, protect your Cloudflare account, and consider Cloudflare Access.
- **No default credentials exist.** The first administrator is created with `create_admin.py`, and login always needs the Telegram code.
- The live-stream endpoint accepts the token in the URL (`?access_token=...`) so the in-app player can use it. URLs can appear in logs and proxies, so do not paste stream URLs into chats.
- MQTT is unauthenticated (see [Step 4](#step-4---mqtt-broker-and-firewall)). Keep it on a trusted LAN.
- Generate your own secrets for every installation: JWT secret, OTA key, Telegram token, Firebase files, and signing keys.
- **Never commit:** `.env`, `google-services.json`, Firebase service-account files, `ota_key.txt`, signing keys, databases, recordings, APKs, or compiled firmware. The included `.gitignore` covers these, but check before every push, for example with `gitleaks detect`.
- Report vulnerabilities privately to the repository owner rather than in a public issue.
- Make sure cameras do not point at places where recording would be illegal or unfair to others, and follow your local rules for CCTV and notices.

## Known limitations

- Hardware encoding is tested on Intel VAAPI only. Other machines use the software fallback and need more CPU.
- Camera firmware has no MQTT authentication, and the stream and setup ports are unencrypted HTTP on the LAN.
- The recording schedule and retention are code constants.
- Streams are MJPEG at modest frame rates (about 10-12 fps at best with the current firmware, which is enough for surveillance).
- Android is the only supported client.
- Several project components were built for one site. Expect small rough edges when adapting them, and please open an issue or a pull request.

## Contributing and license

Bug reports, setup problems, and pull requests are welcome. Please do not include real hostnames, IP addresses, tokens, or camera footage in issues.

Released under the [MIT License](LICENSE).
