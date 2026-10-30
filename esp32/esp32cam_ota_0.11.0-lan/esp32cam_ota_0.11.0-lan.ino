/*
 * SurvCam LAN camera firmware (W5500 SPI Ethernet) - same features as the WiFi
 * build 0.11.0-ota, for cameras that are wired instead of on WiFi:
 *   - camera ID / name / password kept in flash (one .bin works for every LAN camera)
 *   - remote update, remote reboot and rollback through signed MQTT commands
 *   - double-press button -> upload / identity page on http://<camera-ip>:8080/update
 *   - automatic rollback if a new firmware does not become healthy
 *   - task watchdog, restart-cause reporting, MQTT-lost restart
 *   - NEW: the camera restarts itself when the LAN cable/switch drops and comes back
 *
 * NOT carried over (WiFi only): WiFiManager, the 5 s config-portal hold, RSSI / channel / BSSID.
 *
 * ARCHITECTURE NOTE:
 * EthernetSPI2 (https://github.com/arhi/EthernetSPI2) is a classic Arduino "Ethernet"
 * library that drives the W5500's own TCP/IP sockets. It does NOT register with ESP-IDF's
 * network stack, so esp_http_server, HTTPClient and WiFiClient cannot be used here.
 * Everything that used them is rewritten on EthernetServer / EthernetClient:
 *   - video stream (port 81)
 *   - upload / identity page (port 8080)
 *   - firmware download for remote updates
 * The sketch is single-threaded, so while a client is being streamed the stream loop itself
 * keeps MQTT, the watchdog, the OTA page and OTA commands running between frames.
 *
 * WIRING (W5500 Lite, 3.3V ONLY - never 5V):
 *   ESP32-CAM   -> W5500
 *   GPIO12      -> MISO
 *   GPIO13      -> MOSI
 *   GPIO14      -> SCLK
 *   GPIO2       -> SCS (CS)
 *   GND         -> GND
 *   3.3V        -> VCC   (own 3.3V regulator, >= 200 mA, common GND; 100uF + 0.1uF near the module)
 *   Optional update button: GPIO15 -> button -> GND (GPIO13 is MOSI now, so the old button pin is gone)
 *
 * FLASHING: GPIO0 to GND + reset as usual, with the W5500's 3.3V disconnected while flashing.
 * The FIRST flash must be by cable with the same OTA-capable partition scheme as your WiFi cameras.
 */

#include <Arduino.h>
#include <EthernetSPI2.h>
#include <PubSubClient.h>
#include "esp_camera.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_task_wdt.h"
#include <Update.h>
#include <Preferences.h>
#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"
#include "mbedtls/md.h"

struct HttpReq {
  String method;
  String path;
  String otaKey;
  uint32_t contentLen;
};

// ===================== ETHERNET =====================
#define ETH_CS_PIN   2          // SCS, per EthernetSPI2's own wiring
uint8_t ethMac[6];              // filled from the chip's own factory MAC in connectEthernet()

// ===================== MQTT SERVER =====================
const char* MQTT_HOST = "YOUR_SERVER_LAN_IP";
const uint16_t MQTT_PORT = 1883;

// ===================== CAMERA IDENTITY =====================
// ONE firmware file works for EVERY camera: the identity below is kept in the camera's
// flash (not in the .bin), is set once from the setup page, and survives updates and rollbacks.
// Leave the DEFAULT_ values EMPTY in the shared build. Fill them in only to pre-set a camera whose
// flash holds no identity yet (e.g. a one-off cable flash), then change them back to "".
#define DEFAULT_CAMERA_ID        ""
#define DEFAULT_CAMERA_NAME      ""
#define DEFAULT_CAMERA_PASSWORD  ""
String CAMERA_ID = "";
String CAMERA_NAME = "";
String CAMERA_PASSWORD = "";
bool identityMissing = true;   // true until a valid camera ID is stored

// ===================== FIRMWARE =====================
const char* FW_VERSION = "0.11.0-ota-lan";

// ===================== TIMING =====================
const unsigned long HEARTBEAT_INTERVAL_MS = 5000;
const uint16_t STREAM_PORT = 81;   // same as the WiFi cameras, so backend/app config doesn't change

// ===================== SELF-HEALING SETTINGS =====================
// LAN link: if the link goes down and then comes back, the camera restarts itself.
// A drop shorter than LAN_DOWN_DEBOUNCE_MS is treated as a blip and ignored (counted in disc_count).
const unsigned long LAN_LINK_POLL_MS       = 200;
const unsigned long LAN_DOWN_DEBOUNCE_MS   = 1000;
// Random delay before that restart, so all LAN cameras behind one switch don't reboot in the same second.
const unsigned long LAN_RECONNECT_JITTER_MS = 3000;
// DHCP lease renewal (Ethernet.maintain()) is checked this often.
const unsigned long DHCP_MAINTAIN_MS       = 1000;

// While running: reboot if the LAN link is up but MQTT has been down this long.
const unsigned long MQTT_LOST_RESTART_MS   = 180000;   // 3 min
// Random extra delay added to the threshold above.
const unsigned long RESTART_JITTER_MS      = 20000;    // 0..20 s

// Task watchdog: reboots if loop() (or connectMQTT, or a stream) stops running.
const uint32_t WDT_TIMEOUT_MS = 60000;

// ===================== OTA (FIRMWARE UPDATE) SETTINGS =====================
// Shared secret with the backend (ota_key.txt or SURVCAM_OTA_KEY on the server).
// Must be 16+ characters. Make one with:  openssl rand -hex 24
// Updates stay disabled while it is the placeholder.
const char* OTA_KEY = "CHANGE-ME-to-a-long-random-key";

const unsigned long OTA_PAGE_WINDOW_MS    = 600000;   // double-press opens the upload page for 10 min
const uint16_t      OTA_PAGE_PORT         = 8080;     // http://<camera-ip>:8080/update
const unsigned long OTA_VALIDATE_AFTER_MS = 120000;   // new firmware must stay healthy this long...
const uint8_t       OTA_MAX_BOOT_TRIES    = 3;        // ...or it is rolled back after this many boots

// Double-press = two short taps; the 2nd press must start within DOUBLE_TAP_GAP_MS of the 1st release.
const unsigned long TAP_MIN_MS            = 30;       // ignore contact bounce
const unsigned long TAP_MAX_MS            = 400;      // a longer press is not a tap
const unsigned long DOUBLE_TAP_GAP_MS     = 500;

// ===================== BUTTON & INDICATOR =====================
// GPIO15 is the only free, camera-safe pin left once the W5500 uses 2/12/13/14.
// The button is OPTIONAL: with nothing wired, the internal pull-up keeps the pin HIGH and nothing happens.
// A camera with no stored identity opens the setup page by itself, so a fresh camera needs no button.
#define OTA_BUTTON_PIN   15
#define FLASH_LED_PIN     4     // Onboard flash LED (AI Thinker ESP32-CAM)

// ===================== CAMERA PINS: AI THINKER ESP32-CAM =====================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ===================== RESTART-CAUSE TRACKING (survives ESP.restart) =====================
// RTC_NOINIT memory keeps its value across software resets / watchdog resets,
// but NOT across a real power cycle (contents are then random -> magic check).
#define RTC_MAGIC 0xC0FFEE42

enum RestartCause : uint8_t {
  CAUSE_NONE = 0,
  CAUSE_MQTT_LOST = 2,             // LAN up but MQTT down too long
  CAUSE_CAMERA_INIT_FAIL = 4,
  CAUSE_OTA = 5,                   // restarted after installing a new firmware
  CAUSE_OTA_FAIL = 6,              // update failed, restarting on the old firmware
  CAUSE_REMOTE_REBOOT = 7,         // restart command from the backend/app
  CAUSE_ROLLBACK = 8,              // switched back to the previous firmware
  CAUSE_LAN_RECONNECT = 9,         // LAN link dropped and came back
  CAUSE_LAN_CONNECT_TIMEOUT = 10,  // boot: W5500 / link / DHCP failed
  CAUSE_LAN_DHCP_LOST = 11         // DHCP lease could not be renewed
};

RTC_NOINIT_ATTR uint32_t rtcMagic;
RTC_NOINIT_ATTR uint8_t  rtcRestartCause;

// Values from the PREVIOUS boot, captured at startup, reported in heartbeats.
uint8_t prevRestartCause = CAUSE_NONE;
esp_reset_reason_t bootResetReason = ESP_RST_UNKNOWN;

// ---- OTA state (reported in every heartbeat) ----
const char* otaState = "idle";           // idle | downloading | uploading | installed | pending_verify | rolled_back | failed
volatile int otaProgress = 0;            // 0-100 while updating
char otaMsg[40] = "";                    // last result, e.g. "ok 0.10.1" or "failed:sha_mismatch"
bool otaPending = false;                 // running a freshly installed firmware not yet proven healthy
volatile bool otaInProgress = false;     // a download/flash is running right now
volatile bool streamBlocked = false;     // ends/refuses video streams during an update

// Requests from MQTT or the button are only noted here; otaLoopTick() carries them out.
volatile bool otaRequested = false;
volatile bool cmdRebootRequested = false;
volatile bool cmdRollbackRequested = false;
volatile bool otaPageStartRequested = false;
String otaReqUrl, otaReqSha, otaReqVersion;
uint32_t otaReqSize = 0;

bool otaPageActive = false;
unsigned long otaPageUntilMs = 0;

// ===================== GLOBALS =====================
EthernetClient ethClient;                 // used by MQTT
PubSubClient mqttClient(ethClient);
EthernetServer streamServer(STREAM_PORT); // video
EthernetServer otaServer(OTA_PAGE_PORT);  // upload / identity page

unsigned long lastHeartbeatMs = 0;
unsigned long lastPageCheckMs = 0;

// Connectivity health tracking
unsigned long lastMqttOkMs = 0;
unsigned long mqttLostRestartMs = MQTT_LOST_RESTART_MS;
bool lanDown = false;
unsigned long lanDownSinceMs = 0;
unsigned long lastLinkPollMs = 0;
unsigned long lastMaintainMs = 0;
uint32_t lanBlipCount = 0;                // link drops this boot that were too short to restart for

// Watchdog state
bool wdtEnabled = false;

// Button tap-tracking state
bool buttonActive = false;
unsigned long buttonPressStartMs = 0;
bool tapPending = false;
unsigned long lastTapReleaseMs = 0;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// Declared early because they are used before their definitions further down.
const char* runningPartLabel();
void otaHandleCommand(const byte* payload, unsigned int length);
void healthCheck();
void checkButton();
void otaLoopTick();
void otaPageService();
void serviceBackground();

// ===================== HELPERS: TOPICS / NAMES =====================

String heartbeatTopic() {
  return String("survcam/cameras/") + CAMERA_ID + "/heartbeat";
}

String commandTopic() {
  return String("survcam/cameras/") + CAMERA_ID + "/cmd";
}

String macString() {
  char b[18];
  snprintf(b, sizeof(b), "%02x:%02x:%02x:%02x:%02x:%02x",
           ethMac[0], ethMac[1], ethMac[2], ethMac[3], ethMac[4], ethMac[5]);
  return String(b);
}

const char* resetReasonStr(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:   return "poweron";
    case ESP_RST_EXT:       return "ext";
    case ESP_RST_SW:        return "sw";
    case ESP_RST_PANIC:     return "panic";
    case ESP_RST_INT_WDT:   return "int_wdt";
    case ESP_RST_TASK_WDT:  return "task_wdt";
    case ESP_RST_WDT:       return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT:  return "brownout";
    case ESP_RST_SDIO:      return "sdio";
    default:                return "unknown";
  }
}

const char* restartCauseStr(uint8_t c) {
  switch (c) {
    case CAUSE_MQTT_LOST:            return "mqtt_lost";
    case CAUSE_CAMERA_INIT_FAIL:     return "camera_init_fail";
    case CAUSE_OTA:                  return "ota_update";
    case CAUSE_OTA_FAIL:             return "ota_failed";
    case CAUSE_REMOTE_REBOOT:        return "remote_reboot";
    case CAUSE_ROLLBACK:             return "rollback";
    case CAUSE_LAN_RECONNECT:        return "lan_reconnect";
    case CAUSE_LAN_CONNECT_TIMEOUT:  return "lan_connect_timeout";
    case CAUSE_LAN_DHCP_LOST:        return "lan_dhcp_lost";
    default:                         return "none";
  }
}

// ===================== RESTART / WATCHDOG HELPERS =====================

void restartWithCause(uint8_t cause) {
  Serial.printf("Restarting, cause=%s\n", restartCauseStr(cause));
  rtcMagic = RTC_MAGIC;
  rtcRestartCause = cause;
  Serial.flush();
  delay(200);
  ESP.restart();
}

// Reads what the previous boot left behind in RTC memory, then clears it.
void loadPreviousBootInfo() {
  bootResetReason = esp_reset_reason();

  if (bootResetReason == ESP_RST_POWERON || rtcMagic != RTC_MAGIC) {
    // Real power-on (or first ever boot): RTC_NOINIT contents are garbage.
    rtcMagic = RTC_MAGIC;
    rtcRestartCause = CAUSE_NONE;
  }

  prevRestartCause = rtcRestartCause;

  // Clear so a later reset that we don't tag (e.g. watchdog, panic)
  // doesn't inherit a stale cause.
  rtcRestartCause = CAUSE_NONE;
}

// Arduino-ESP32 3.x (IDF 5.x): the task watchdog is already initialised by
// the core, so we reconfigure it instead of calling esp_task_wdt_init(sec, bool).
void setupWatchdog() {
  esp_task_wdt_config_t wdtConfig = {};
  wdtConfig.timeout_ms = WDT_TIMEOUT_MS;
  wdtConfig.idle_core_mask = 0;     // don't watch the idle tasks, only our loop task
  wdtConfig.trigger_panic = true;   // reboot on timeout

  esp_err_t err = esp_task_wdt_reconfigure(&wdtConfig);
  if (err == ESP_ERR_INVALID_STATE) {
    // Watchdog wasn't initialised yet -> initialise it with our config.
    err = esp_task_wdt_init(&wdtConfig);
  }
  if (err != ESP_OK) {
    Serial.printf("Watchdog config failed: 0x%x\n", err);
    return;
  }

  err = esp_task_wdt_add(NULL);     // subscribe the current (loop) task
  if (err == ESP_OK) {
    wdtEnabled = true;
    Serial.printf("Task watchdog enabled (%u ms)\n", (unsigned)WDT_TIMEOUT_MS);
  } else {
    Serial.printf("Watchdog add failed: 0x%x\n", err);
  }
}

inline void wdtFeed() {
  if (wdtEnabled) esp_task_wdt_reset();
}

// ===================== CAMERA =====================

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;

  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 17;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);

  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();

  if (s) {
    s->set_framesize(s, psramFound() ? FRAMESIZE_SVGA : FRAMESIZE_VGA);
    s->set_hmirror(s, 1);    // 1 for normal, 0 for flip
    //s->set_vflip(s, 1);     // new line for flip
    s->set_brightness(s, 0);
    s->set_contrast(s, 0);
    s->set_saturation(s, 0);
  }

  Serial.println("Camera initialized");
  return true;
}

void blinkFlashOnce() {
  digitalWrite(FLASH_LED_PIN, HIGH);
  delay(200);
  digitalWrite(FLASH_LED_PIN, LOW);
}

// ===================== ETHERNET: CONNECT, LINK MONITOR, HEALTH CHECK =====================

// Boot-time connect. Bounded: W5500 init + DHCP (up to ~60 s), then reboot if it failed.
void connectEthernet() {
  // The chip's own factory MAC: unique per camera, so the same .bin on every LAN camera
  // can never produce two cameras with the same MAC (which gave them the same DHCP IP).
  esp_read_mac(ethMac, ESP_MAC_ETH);
  Serial.print("Ethernet MAC: ");
  Serial.println(macString());

  Ethernet.init(ETH_CS_PIN);

  Serial.println("Starting Ethernet (W5500) + DHCP...");
  if (Ethernet.begin(ethMac) == 0) {
    // begin() returns 0 both when the W5500 does not answer and when DHCP gave no lease.
    if (Ethernet.hardwareStatus() == EthernetNoHardware) {
      Serial.println("W5500 not detected - check SPI wiring (12/13/14/2) and the 3.3V supply");
    } else if (Ethernet.linkStatus() == LinkOFF) {
      Serial.println("Ethernet cable not connected / no link");
    } else {
      Serial.println("DHCP did not give an IP address");
    }
    for (int i = 0; i < 10; i++) {   // visible error blink, about 10 s
      blinkFlashOnce();
      delay(800);
    }
    // Random delay so cameras behind one switch don't all retry in lockstep.
    delay(random(0, 10000));
    restartWithCause(CAUSE_LAN_CONNECT_TIMEOUT);
    return;
  }

  lanDown = false;
  Serial.print("Ethernet connected, IP: ");
  Serial.println(Ethernet.localIP());
}

// If the LAN link goes down and then comes back, restart: after such a drop the camera
// does not recover by itself. Short blips (< LAN_DOWN_DEBOUNCE_MS) are ignored.
void lanLinkMonitor() {
  unsigned long now = millis();
  if (now - lastLinkPollMs < LAN_LINK_POLL_MS) return;
  lastLinkPollMs = now;

  bool up = (Ethernet.linkStatus() != LinkOFF);

  if (!up) {
    if (!lanDown) {
      lanDown = true;
      lanDownSinceMs = now;
      Serial.println("LAN link lost");
    }
    return;
  }

  if (lanDown) {
    if (now - lanDownSinceMs >= LAN_DOWN_DEBOUNCE_MS) {
      Serial.println("LAN link is back after a drop -> restarting to recover");
      wdtFeed();
      delay(random(500, LAN_RECONNECT_JITTER_MS));
      restartWithCause(CAUSE_LAN_RECONNECT);
    } else {
      lanDown = false;
      lanBlipCount++;
      Serial.println("LAN link blip ignored (very short)");
    }
  }
}

// Call frequently: from loop(), from inside the stream loop, and from any blocking retry loop.
// - feeds the watchdog
// - restarts after a LAN drop + reconnect
// - renews the DHCP lease
// - restarts if the LAN is up but MQTT has been down too long
void healthCheck() {
  wdtFeed();

  unsigned long now = millis();

  if (otaInProgress) {   // a download/flash is running: don't treat it as an MQTT outage
    lastMqttOkMs = now;
    return;
  }

  lanLinkMonitor();

  // DHCP leases must be renewed from the sketch with the Arduino Ethernet library.
  if (!lanDown && now - lastMaintainMs >= DHCP_MAINTAIN_MS) {
    lastMaintainMs = now;
    int m = Ethernet.maintain();   // 1 = renew failed, 2 = renewed, 3 = rebind failed, 4 = rebound
    if (m == 1) {
      Serial.println("DHCP renew failed (will retry)");
    } else if (m == 3) {
      Serial.println("DHCP rebind failed - lease lost");
      restartWithCause(CAUSE_LAN_DHCP_LOST);
    }
  }

  // MQTT timer only runs while the LAN link is up (a LAN drop has its own rule).
  if (lanDown || mqttClient.connected() || identityMissing) {
    lastMqttOkMs = now;
  } else if (now - lastMqttOkMs > mqttLostRestartMs) {
    Serial.println("MQTT down too long while LAN is up");
    restartWithCause(CAUSE_MQTT_LOST);
  }
}

// Two short taps = open the update/identity page. (The WiFi build's 5 s hold is not needed on LAN.)
void checkButton() {
  bool pressed = (digitalRead(OTA_BUTTON_PIN) == LOW);   // pressed = pulled to GND

  if (pressed) {
    if (!buttonActive) {
      buttonActive = true;
      buttonPressStartMs = millis();
    }
    return;
  }

  if (buttonActive) {
    // Button was just released: is this the second tap of a double-press?
    unsigned long now = millis();
    unsigned long heldMs = now - buttonPressStartMs;

    if (heldMs >= TAP_MIN_MS && heldMs <= TAP_MAX_MS) {
      if (tapPending && (buttonPressStartMs - lastTapReleaseMs) <= DOUBLE_TAP_GAP_MS) {
        tapPending = false;
        otaPageStartRequested = true;   // opened from otaLoopTick()
      } else {
        tapPending = true;
        lastTapReleaseMs = now;
      }
    } else {
      tapPending = false;
    }
    buttonActive = false;
  }
}

// Drop-in replacement for delay(ms) that keeps the health check, button and update page
// running in small increments instead of blocking blind.
void waitWithButtonCheck(unsigned long ms) {
  unsigned long start = millis();

  while (millis() - start < ms) {
    healthCheck();
    checkButton();
    otaLoopTick();
    otaPageService();
    delay(50);
  }
}

// ===================== MQTT =====================

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  Serial.print("MQTT command received on ");
  Serial.print(topic);
  Serial.print(": ");

  for (unsigned int i = 0; i < length; i++) {
    Serial.print((char)payload[i]);
  }

  Serial.println();

  otaHandleCommand(payload, length);   // update / reboot / rollback (signed commands only)
}

String buildHeartbeatPayload(const char* status) {
  String payload = "{";
  payload += "\"status\":\"";
  payload += status;
  payload += "\",";
  payload += "\"camera_id\":\"";
  payload += CAMERA_ID;
  payload += "\",";
  payload += "\"name\":\"";
  payload += CAMERA_NAME;
  payload += "\",";
  payload += "\"password\":\"";
  payload += CAMERA_PASSWORD;
  payload += "\",";
  payload += "\"ip_address\":\"";
  payload += Ethernet.localIP().toString();
  payload += "\",";
  payload += "\"stream_port\":";
  payload += String(STREAM_PORT);
  payload += ",";
  payload += "\"stream_path\":\"/stream\",";
  payload += "\"uptime_ms\":";
  payload += String(millis());
  payload += ",";
  payload += "\"fw_version\":\"";
  payload += FW_VERSION;
  payload += "\"";

  // ---- LAN identification (new keys; the WiFi-only rssi / channel / bssid are not sent) ----
  payload += ",\"net\":\"lan\",\"mac\":\"";
  payload += macString();
  payload += "\"";

  // ---- diagnostics: why did this camera last reboot? ----
  payload += ",\"reset_reason\":";
  payload += String((int)bootResetReason);
  payload += ",\"reset_reason_str\":\"";
  payload += resetReasonStr(bootResetReason);
  payload += "\",\"restart_cause\":\"";
  payload += restartCauseStr(prevRestartCause);   // our own reason for the last reboot
  // Kept so the backend keeps receiving the same keys: the WiFi disconnect reasons do not
  // exist on LAN (0), and disc_count here = very short LAN link blips this boot.
  payload += "\",\"prev_disc_reason\":0";
  payload += ",\"disc_reason\":0";
  payload += ",\"disc_count\":";
  payload += String((unsigned long)lanBlipCount);
  payload += ",\"free_heap\":";
  payload += String(ESP.getFreeHeap());

  // ---- chip temperature (the original ESP32 often has no working sensor: send null then) ----
  float chipTemp = temperatureRead();
  if (isnan(chipTemp) || (chipTemp > 53.2f && chipTemp < 53.4f)) {
    payload += ",\"temp_c\":null";
  } else {
    payload += ",\"temp_c\":";
    payload += String(chipTemp, 1);
  }

  // ---- firmware update status ----
  payload += ",\"ota_state\":\"";
  payload += otaState;
  payload += "\",\"ota_progress\":";
  payload += String((int)otaProgress);
  payload += ",\"ota_part\":\"";
  payload += runningPartLabel();
  payload += "\",\"ota_msg\":\"";
  payload += otaMsg;
  payload += "\"";

  payload += "}";

  return payload;
}

void publishHeartbeat(const char* status) {
  String topic = heartbeatTopic();
  String payload = buildHeartbeatPayload(status);

  bool ok = mqttClient.publish(topic.c_str(), payload.c_str(), true);

  Serial.print("Heartbeat publish ");
  Serial.print(ok ? "OK" : "FAILED");
  Serial.print(" -> ");
  Serial.println(payload);
}

void connectMQTT() {
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMqttMessage);
  mqttClient.setBufferSize(1024);
  mqttClient.setSocketTimeout(10);
  mqttClient.setKeepAlive(30);

  while (!mqttClient.connected()) {
    // This loop can block for a long time, so it must run the health check
    // itself (watchdog feed + LAN/MQTT restart rules).
    healthCheck();

    // LAN dropped: go back to loop() instead of spinning here forever.
    if (lanDown) {
      return;
    }

    checkButton();

    Serial.print("Connecting to MQTT: ");
    Serial.print(MQTT_HOST);
    Serial.print(":");
    Serial.println(MQTT_PORT);

    String clientId = String("esp32cam-") + CAMERA_ID;

    String willTopic = heartbeatTopic();
    String willPayload = buildHeartbeatPayload("offline");

    bool connected = mqttClient.connect(
      clientId.c_str(),
      willTopic.c_str(),
      1,
      true,
      willPayload.c_str()
    );

    if (connected) {
      Serial.println("MQTT connected");

      mqttClient.subscribe(commandTopic().c_str());
      Serial.print("Subscribed to: ");
      Serial.println(commandTopic());

      publishHeartbeat("online");
    } else {
      Serial.print("MQTT failed, rc=");
      Serial.println(mqttClient.state());
      waitWithButtonCheck(3000); // 3s backoff, but stays responsive to button + health check + update page
    }
  }
}

// ===================== OTA: FIRMWARE UPDATES, REMOTE REBOOT, ROLLBACK =====================
// Three ways in:
//   1. Remote : backend sends a SIGNED "ota" command on survcam/cameras/<id>/cmd; the camera
//               downloads the .bin from the backend, verifies SHA-256 and installs it.
//   2. Local  : double-press the button -> upload page http://<camera-ip>:8080/update for 10 min.
//   3. Cable  : unchanged.
// Safety net: a freshly installed firmware must stay healthy (LAN + MQTT connected) for
// OTA_VALIDATE_AFTER_MS. If it reboots OTA_MAX_BOOT_TRIES times first, the camera switches
// back to the previous firmware by itself.

Preferences otaPrefs;

bool otaKeyConfigured() {
  return strlen(OTA_KEY) >= 16 && strcmp(OTA_KEY, "CHANGE-ME-to-a-long-random-key") != 0;
}

const char* runningPartLabel() {
  const esp_partition_t* p = esp_ota_get_running_partition();
  return p ? p->label : "?";
}

void setOtaMsg(const String& m) {
  String clean = m;
  clean.replace("\"", "'");
  clean.replace("\\", "/");
  strncpy(otaMsg, clean.c_str(), sizeof(otaMsg) - 1);
  otaMsg[sizeof(otaMsg) - 1] = 0;
}

// Constant-time string compare (for signatures and the upload key).
bool constEq(const String& a, const String& b) {
  if (a.length() != b.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < a.length(); i++) diff |= (uint8_t)(a[i] ^ b[i]);
  return diff == 0;
}

// HMAC-SHA256 of msg with OTA_KEY, as 64 lowercase hex characters.
bool otaHmacHex(const String& msg, char* out) {
  unsigned char mac[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (!info) return false;
  if (mbedtls_md_hmac(info, (const unsigned char*)OTA_KEY, strlen(OTA_KEY),
                      (const unsigned char*)msg.c_str(), msg.length(), mac) != 0) {
    return false;
  }
  for (int i = 0; i < 32; i++) snprintf(out + i * 2, 3, "%02x", mac[i]);
  out[64] = 0;
  return true;
}

// Tiny readers for the flat JSON the backend sends: {"cmd":"ota","seq":123,...}
String jsonStr(const String& s, const char* key) {
  String pat = String("\"") + key + "\"";
  int k = s.indexOf(pat);
  if (k < 0) return "";
  int c = s.indexOf(':', k + pat.length());
  if (c < 0) return "";
  int q1 = s.indexOf('"', c + 1);
  if (q1 < 0) return "";
  for (int i = c + 1; i < q1; i++) {
    if (s[i] != ' ') return "";   // value is not a string
  }
  int q2 = s.indexOf('"', q1 + 1);
  if (q2 < 0) return "";
  return s.substring(q1 + 1, q2);
}

uint32_t jsonNum(const String& s, const char* key) {
  String pat = String("\"") + key + "\"";
  int k = s.indexOf(pat);
  if (k < 0) return 0;
  int c = s.indexOf(':', k + pat.length());
  if (c < 0) return 0;
  return (uint32_t)strtoul(s.c_str() + c + 1, NULL, 10);
}

// ---- persistent state (NVS) ----

uint32_t otaGetLastSeq() {
  otaPrefs.begin("ota", false);
  uint32_t v = otaPrefs.getULong("lastseq", 0);
  otaPrefs.end();
  return v;
}

void otaSetLastSeq(uint32_t v) {
  otaPrefs.begin("ota", false);
  otaPrefs.putULong("lastseq", v);
  otaPrefs.end();
}

void otaMarkInstalled() {
  otaPrefs.begin("ota", false);
  otaPrefs.putUChar("pending", 1);
  otaPrefs.putUChar("tries", 0);
  otaPrefs.putUChar("rolled", 0);
  otaPrefs.end();
}

void otaRecordFailure(const String& why) {
  otaPrefs.begin("ota", false);
  otaPrefs.putString("lastmsg", String("failed:") + why);
  otaPrefs.end();
}

void otaMarkHealthy() {
  otaPrefs.begin("ota", false);
  otaPrefs.putUChar("pending", 0);
  otaPrefs.putUChar("tries", 0);
  otaPrefs.putString("lastmsg", String("ok ") + FW_VERSION);
  otaPrefs.end();
  otaPending = false;
  otaState = "idle";
  setOtaMsg(String("ok ") + FW_VERSION);
  Serial.println("New firmware verified healthy");
}

// Switch back to the other firmware slot and restart. Does not return on success.
void otaRollbackNow(const char* why) {
  const esp_partition_t* other = esp_ota_get_next_update_partition(NULL);
  if (other != NULL && esp_ota_set_boot_partition(other) == ESP_OK) {
    otaPrefs.begin("ota", false);
    otaPrefs.putUChar("pending", 0);
    otaPrefs.putUChar("tries", 0);
    otaPrefs.putUChar("rolled", 1);
    otaPrefs.putString("lastmsg", String("rolled_back:") + why);
    otaPrefs.end();
    restartWithCause(CAUSE_ROLLBACK);
  }
  Serial.println("Rollback failed: no valid previous firmware");
  setOtaMsg("rollback_failed");
}

// Call first thing in setup().
void otaBootCheck() {
  otaPrefs.begin("ota", false);
  uint8_t pending = otaPrefs.getUChar("pending", 0);
  uint8_t tries = otaPrefs.getUChar("tries", 0);
  uint8_t rolled = otaPrefs.getUChar("rolled", 0);
  String last = otaPrefs.getString("lastmsg", "");

  if (rolled) otaPrefs.putUChar("rolled", 0);
  if (last.startsWith("failed") || last.startsWith("rolled")) otaPrefs.putString("lastmsg", "");
  if (pending) {
    tries++;
    otaPrefs.putUChar("tries", tries);
  }
  otaPrefs.end();

  if (last.length() > 0) setOtaMsg(last);
  if (rolled) otaState = "rolled_back";
  else if (last.startsWith("failed")) otaState = "failed";

  Serial.printf("Firmware slot: %s, version %s, OTA pending=%d tries=%d\n",
                runningPartLabel(), FW_VERSION, (int)pending, (int)tries);

  if (pending) {
    if (tries > OTA_MAX_BOOT_TRIES) {
      Serial.println("New firmware did not become healthy in time -> rolling back");
      otaRollbackNow("unhealthy");   // restarts on success
    }
    otaPending = true;
    otaState = "pending_verify";
  }

  if (!otaKeyConfigured()) {
    Serial.println("NOTE: OTA_KEY not set in the sketch - remote/browser updates are disabled");
  }
}

// ---- shared steps of every update ----

void otaPrepare() {
  otaInProgress = true;
  streamBlocked = true;   // ends running video streams, refuses new ones
  wdtFeed();
  delay(1500);
  esp_camera_deinit();    // free the camera while updating
}

// Everything runs in the one main task on LAN, so MQTT can always be used here.
void otaFailAndRestart(const String& why) {
  Serial.println("OTA failed: " + why);
  if (Update.isRunning()) Update.abort();
  setOtaMsg("failed:" + why);
  otaState = "failed";
  otaRecordFailure(why);
  if (mqttClient.connected()) {
    publishHeartbeat("online");
    mqttClient.loop();
  }
  delay(500);
  restartWithCause(CAUSE_OTA_FAIL);
}

void otaFinishAndRestart(const String& msg) {
  otaMarkInstalled();
  otaState = "installed";
  otaProgress = 100;
  setOtaMsg(msg);
  Serial.println("OTA installed: " + msg);
  if (mqttClient.connected()) {
    publishHeartbeat("online");
    mqttClient.loop();
  }
  delay(500);
  restartWithCause(CAUSE_OTA);
}

// ---- camera identity (kept in flash, so ONE firmware file works for every camera) ----

Preferences identPrefs;

bool validCameraId(const String& id) {
  if (id.length() < 3 || id.length() > 24) return false;
  for (unsigned int i = 0; i < id.length(); i++) {
    char c = id[i];
    bool ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '-';
    if (!ok) return false;
  }
  return true;
}

// Drops characters that would break the heartbeat JSON, then trims and limits the length.
String cleanIdentityText(const String& s, unsigned int maxLen) {
  String out;
  for (unsigned int i = 0; i < s.length() && out.length() < maxLen; i++) {
    char c = s[i];
    if (c == '"' || c == '\\' || c < 32) continue;
    out += c;
  }
  out.trim();
  return out;
}

String htmlEscape(const String& s) {
  String out;
  for (unsigned int i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') out += "&amp;";
    else if (c == '<') out += "&lt;";
    else if (c == '>') out += "&gt;";
    else if (c == '\'') out += "&#39;";
    else if (c == '"') out += "&quot;";
    else out += c;
  }
  return out;
}

// Call early in setup(). Sets identityMissing when no valid camera ID is stored.
void loadIdentity() {
  identPrefs.begin("ident", false);
  CAMERA_ID = identPrefs.getString("id", "");
  CAMERA_NAME = identPrefs.getString("name", "");
  CAMERA_PASSWORD = identPrefs.getString("pw", "");

  // Optional one-off seeding from the DEFAULT_ values at the top of the sketch.
  if (!validCameraId(CAMERA_ID) && validCameraId(String(DEFAULT_CAMERA_ID))) {
    CAMERA_ID = DEFAULT_CAMERA_ID;
    CAMERA_NAME = DEFAULT_CAMERA_NAME;
    CAMERA_PASSWORD = DEFAULT_CAMERA_PASSWORD;
    identPrefs.putString("id", CAMERA_ID);
    identPrefs.putString("name", CAMERA_NAME);
    identPrefs.putString("pw", CAMERA_PASSWORD);
  }
  identPrefs.end();

  identityMissing = !validCameraId(CAMERA_ID);
  if (identityMissing) CAMERA_ID = "";
}

void saveIdentity(const String& id, const String& name, const String& pw) {
  identPrefs.begin("ident", false);
  identPrefs.putString("id", id);
  identPrefs.putString("name", name);
  identPrefs.putString("pw", pw);
  identPrefs.end();
}

// ---- tiny HTTP helpers on EthernetClient (used by the update page and the firmware download) ----



// Reads one line (up to LF). Returns false on timeout or disconnect.
bool readHttpLine(EthernetClient& c, String& line, unsigned long timeoutMs) {
  line = "";
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    wdtFeed();
    if (c.available()) {
      int ch = c.read();
      if (ch < 0) continue;
      if (ch == '\n') {
        if (line.endsWith("\r")) line.remove(line.length() - 1);
        return true;
      }
      if (line.length() < 300) line += (char)ch;
    } else {
      if (!c.connected()) return false;
      delay(1);
    }
  }
  return false;
}

// Reads the request line and headers (stops right after the blank line, body is left unread).
bool readHttpRequest(EthernetClient& c, HttpReq& r) {
  r.method = "";
  r.path = "";
  r.otaKey = "";
  r.contentLen = 0;

  String line;
  if (!readHttpLine(c, line, 5000)) return false;

  int s1 = line.indexOf(' ');
  int s2 = (s1 >= 0) ? line.indexOf(' ', s1 + 1) : -1;
  if (s1 < 0 || s2 < 0) return false;
  r.method = line.substring(0, s1);
  r.path = line.substring(s1 + 1, s2);
  int q = r.path.indexOf('?');
  if (q >= 0) r.path = r.path.substring(0, q);

  for (int i = 0; i < 40; i++) {
    if (!readHttpLine(c, line, 5000)) return false;
    if (line.length() == 0) return true;   // end of headers
    int colon = line.indexOf(':');
    if (colon <= 0) continue;
    String name = line.substring(0, colon);
    name.toLowerCase();
    String value = line.substring(colon + 1);
    value.trim();
    if (name == "content-length") r.contentLen = (uint32_t)strtoul(value.c_str(), NULL, 10);
    else if (name == "x-ota-key") r.otaKey = value;
  }
  return false;
}

void sendHttp(EthernetClient& c, int code, const char* statusText, const char* contentType, const String& body) {
  c.print("HTTP/1.1 ");
  c.print(code);
  c.print(" ");
  c.println(statusText);
  c.print("Content-Type: ");
  c.println(contentType);
  c.print("Content-Length: ");
  c.println((unsigned)body.length());
  c.println("Connection: close");
  c.println();

  const uint8_t* p = (const uint8_t*)body.c_str();
  size_t left = body.length();
  while (left > 0) {
    size_t n = left > 1024 ? 1024 : left;
    c.write(p, n);
    p += n;
    left -= n;
  }
  c.flush();
}

// ---- 2. local upload / identity page (double-press) ----

void handlePageGet(EthernetClient& c) {
  String page = F("<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
                  "<title>SurvCam setup</title></head>"
                  "<body style='font-family:sans-serif;max-width:480px;margin:20px auto;padding:0 12px'>"
                  "<h3>SurvCam camera setup (LAN)</h3><p>Camera: ");
  page += identityMissing ? String("(no ID stored yet)") : htmlEscape(CAMERA_ID);
  page += "<br>Running: ";
  page += FW_VERSION;
  page += " (slot ";
  page += runningPartLabel();
  page += ")</p>";
  page += F("<input type=password id=k placeholder='OTA key' style='width:100%;padding:8px'>"
            "<h4>Camera identity</h4>"
            "<input id=i placeholder='Camera ID, e.g. cam012' style='width:100%;padding:8px' value='");
  page += htmlEscape(CAMERA_ID);
  page += F("'><br><br><input id=n placeholder='Name, e.g. Porch 2' style='width:100%;padding:8px' value='");
  page += htmlEscape(CAMERA_NAME);
  page += F("'><br><br><input id=p placeholder='Camera password' style='width:100%;padding:8px' value='");
  page += htmlEscape(CAMERA_PASSWORD);
  page += F("'><br><br><button onclick='sv()' style='padding:10px 20px'>Save identity and restart</button>"
            "<h4>Firmware file</h4>"
            "<input type=file id=f accept='.bin'><br><br>"
            "<button onclick='up()' style='padding:10px 20px'>Upload and install</button><pre id=s></pre>"
            "<script>var s=document.getElementById('s');"
            "function key(){return document.getElementById('k').value}"
            "function up(){var f=document.getElementById('f').files[0];"
            "if(!f){s.textContent='Choose the .bin file first';return}"
            "var x=new XMLHttpRequest();x.open('POST','/update');x.setRequestHeader('X-OTA-Key',key());"
            "x.upload.onprogress=function(e){if(e.lengthComputable){s.textContent='Uploading '+Math.round(e.loaded/e.total*100)+'%'}};"
            "x.onload=function(){s.textContent=x.responseText};"
            "x.onerror=function(){s.textContent='Connection lost - the camera may be restarting. Check its version in the app in a minute.'};"
            "x.send(f)}"
            "function sv(){var x=new XMLHttpRequest();x.open('POST','/identity');"
            "x.setRequestHeader('X-OTA-Key',key());x.setRequestHeader('Content-Type','application/json');"
            "x.onload=function(){s.textContent=x.responseText};"
            "x.onerror=function(){s.textContent='Connection lost - the camera may be restarting.'};"
            "x.send(JSON.stringify({id:document.getElementById('i').value,name:document.getElementById('n').value,pw:document.getElementById('p').value}))}"
            "</script></body></html>");
  sendHttp(c, 200, "OK", "text/html", page);
}

void handleIdentityPost(EthernetClient& c, const HttpReq& r) {
  if (!otaKeyConfigured() || !constEq(r.otaKey, String(OTA_KEY))) {
    sendHttp(c, 401, "Unauthorized", "text/plain", "Wrong OTA key");
    return;
  }

  size_t total = r.contentLen;
  if (total == 0 || total > 400) {
    sendHttp(c, 400, "Bad Request", "text/plain", "Bad request size");
    return;
  }

  char body[401];
  size_t got = 0;
  unsigned long lastData = millis();
  while (got < total) {
    wdtFeed();
    if (c.available() > 0) {
      int n = c.read((uint8_t*)body + got, total - got);
      if (n > 0) {
        got += n;
        lastData = millis();
      }
    } else {
      if (!c.connected() || millis() - lastData > 5000) {
        sendHttp(c, 400, "Bad Request", "text/plain", "Request timed out");
        return;
      }
      delay(1);
    }
  }
  body[got] = 0;

  String s = String(body);
  String id = cleanIdentityText(jsonStr(s, "id"), 24);
  String name = cleanIdentityText(jsonStr(s, "name"), 40);
  String pw = cleanIdentityText(jsonStr(s, "pw"), 24);

  if (!validCameraId(id)) {
    sendHttp(c, 400, "Bad Request", "text/plain", "Camera ID must be 3-24 letters, digits, - or _");
    return;
  }
  if (name.length() == 0) name = id;

  saveIdentity(id, name, pw);
  sendHttp(c, 200, "OK", "text/plain", "Saved. The camera is restarting and will come back as this camera.");
  c.stop();
  delay(500);
  restartWithCause(CAUSE_NONE);
}

void handleUploadPost(EthernetClient& c, const HttpReq& r) {
  if (otaInProgress) {
    sendHttp(c, 400, "Bad Request", "text/plain", "An update is already running");
    return;
  }
  if (!otaKeyConfigured() || !constEq(r.otaKey, String(OTA_KEY))) {
    sendHttp(c, 401, "Unauthorized", "text/plain", "Wrong OTA key");
    return;
  }

  size_t total = r.contentLen;
  const esp_partition_t* target = esp_ota_get_next_update_partition(NULL);
  if (total < 100000 || target == NULL || total > target->size) {
    sendHttp(c, 400, "Bad Request", "text/plain", "File size is not valid for this camera");
    return;
  }

  otaState = "uploading";
  otaProgress = 0;
  setOtaMsg("browser upload");
  otaPrepare();

  if (!Update.begin(total)) {
    sendHttp(c, 500, "Internal Server Error", "text/plain", "Could not start the update");
    otaFailAndRestart("update_begin");
    return;
  }

  uint8_t* buf = (uint8_t*)malloc(2048);
  if (!buf) {
    sendHttp(c, 500, "Internal Server Error", "text/plain", "Out of memory");
    otaFailAndRestart("no_memory");
    return;
  }

  size_t remaining = total;
  unsigned long lastData = millis();
  while (remaining > 0) {
    wdtFeed();
    int avail = c.available();
    if (avail > 0) {
      size_t want = remaining < 2048 ? remaining : 2048;
      if ((size_t)avail < want) want = (size_t)avail;
      int n = c.read(buf, want);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) {
        free(buf);
        sendHttp(c, 500, "Internal Server Error", "text/plain", "Flash write failed (is this a camera .bin?)");
        otaFailAndRestart("write_failed");
        return;
      }
      remaining -= n;
      lastData = millis();
      otaProgress = (int)(((total - remaining) * 100ULL) / total);
    } else {
      if (!c.connected()) {
        free(buf);
        otaFailAndRestart("upload_aborted");
        return;
      }
      if (millis() - lastData > 15000) {
        free(buf);
        otaFailAndRestart("upload_stalled");
        return;
      }
      delay(1);
    }
  }
  free(buf);

  if (!Update.end(true)) {
    sendHttp(c, 500, "Internal Server Error", "text/plain", "Image check failed");
    otaFailAndRestart("update_end");
    return;
  }

  sendHttp(c, 200, "OK", "text/plain", "Update installed. The camera is restarting; the app will show the new version in a minute or two.");
  c.stop();
  otaFinishAndRestart("installed (browser)");
}

// Answers one pending request on port 8080, if any. Called between video frames too.
void otaPageService() {
  if (otaInProgress) return;

  EthernetClient c = otaServer.available();
  if (!c) return;

  HttpReq r;
  if (!readHttpRequest(c, r)) {
    sendHttp(c, 400, "Bad Request", "text/plain", "Bad request");
    c.stop();
    return;
  }

  // The port always listens (EthernetServer cannot be closed), but it only answers while the page is open.
  if (!otaPageActive || ((long)(millis() - otaPageUntilMs) >= 0 && !identityMissing)) {
    sendHttp(c, 403, "Forbidden", "text/plain", "Update page is closed - double-press the button again");
    c.stop();
    return;
  }

  bool isUpdate = (r.path == "/update");
  bool isIdent = (r.path == "/identity");

  if (r.method == "GET" && (isUpdate || isIdent)) {
    handlePageGet(c);
  } else if (r.method == "POST" && isIdent) {
    handleIdentityPost(c, r);
  } else if (r.method == "POST" && isUpdate) {
    handleUploadPost(c, r);
  } else {
    sendHttp(c, 404, "Not Found", "text/plain", "Not found");
  }

  delay(5);
  c.stop();
}

void startOtaPage() {
  if (!otaKeyConfigured()) {
    Serial.println("Update page not started: OTA_KEY is not set in the sketch");
    return;
  }

  otaPageUntilMs = millis() + OTA_PAGE_WINDOW_MS;   // a second double-press extends the time
  if (otaPageActive) return;

  otaPageActive = true;
  Serial.printf("Update page open for %lu min: http://%s:%u/update\n",
                OTA_PAGE_WINDOW_MS / 60000UL, Ethernet.localIP().toString().c_str(), (unsigned)OTA_PAGE_PORT);
  for (int i = 0; i < 3; i++) {   // three quick flashes = page is open
    blinkFlashOnce();
    delay(150);
  }
}

void stopOtaPage() {
  otaPageActive = false;
  Serial.println("Update page closed");
}

// ---- 1. remote update: download from the backend (plain http://host[:port]/path) ----

bool parseHttpUrl(const String& url, String& host, uint16_t& port, String& path) {
  if (!url.startsWith("http://")) return false;
  String rest = url.substring(7);
  int slash = rest.indexOf('/');
  String hostPort = (slash < 0) ? rest : rest.substring(0, slash);
  path = (slash < 0) ? String("/") : rest.substring(slash);

  int colon = hostPort.indexOf(':');
  if (colon < 0) {
    host = hostPort;
    port = 80;
  } else {
    host = hostPort.substring(0, colon);
    port = (uint16_t)hostPort.substring(colon + 1).toInt();
  }
  return host.length() > 0 && port != 0;
}

void otaDownloadFail(EthernetClient& dl, const String& why) {
  dl.stop();
  otaFailAndRestart(why);
}

bool otaDownloadAndFlash() {
  String host, path;
  uint16_t port = 80;
  if (!parseHttpUrl(otaReqUrl, host, port, path)) {
    otaFailAndRestart("bad_url");
    return false;
  }

  EthernetClient dl;   // its own W5500 socket, separate from the MQTT connection

  IPAddress ip;
  bool connectedOk;
  if (ip.fromString(host)) connectedOk = dl.connect(ip, port);
  else connectedOk = dl.connect(host.c_str(), port);
  if (!connectedOk) {
    otaFailAndRestart("connect_failed");
    return false;
  }

  dl.print("GET ");
  dl.print(path);
  dl.println(" HTTP/1.1");
  dl.print("Host: ");
  dl.println(host);
  dl.println("Connection: close");
  dl.println();

  // ---- status line + headers ----
  String line;
  if (!readHttpLine(dl, line, 10000)) {
    otaDownloadFail(dl, "no_response");
    return false;
  }
  int sp = line.indexOf(' ');
  int code = (sp >= 0) ? line.substring(sp + 1).toInt() : 0;
  if (code != 200) {
    otaDownloadFail(dl, "http_" + String(code));
    return false;
  }

  int total = -1;
  bool chunked = false;
  for (int i = 0; i < 40; i++) {
    if (!readHttpLine(dl, line, 10000)) {
      otaDownloadFail(dl, "bad_headers");
      return false;
    }
    if (line.length() == 0) break;
    String lower = line;
    lower.toLowerCase();
    if (lower.startsWith("content-length:")) total = lower.substring(15).toInt();
    if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) chunked = true;
  }

  const esp_partition_t* target = esp_ota_get_next_update_partition(NULL);
  if (chunked || total <= 0 || (otaReqSize != 0 && (uint32_t)total != otaReqSize)) {
    otaDownloadFail(dl, chunked ? "chunked" : "size_mismatch");
    return false;
  }
  if (target == NULL || (uint32_t)total > target->size) {
    otaDownloadFail(dl, "too_big");
    return false;
  }
  if (!Update.begin(total)) {
    otaDownloadFail(dl, "update_begin");
    return false;
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);

  uint8_t* buf = (uint8_t*)malloc(1460);
  if (!buf) {
    otaDownloadFail(dl, "no_memory");
    return false;
  }

  int written = 0;
  unsigned long lastData = millis();
  unsigned long lastReport = 0;

  while (written < total) {
    wdtFeed();
    mqttClient.loop();    // keep the MQTT connection alive during the download

    int avail = dl.available();
    if (avail > 0) {
      int want = avail < 1460 ? avail : 1460;
      if (want > total - written) want = total - written;
      int n = dl.read(buf, want);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) {
        free(buf);
        otaDownloadFail(dl, "write_failed");
        return false;
      }
      mbedtls_sha256_update(&sha, buf, n);
      written += n;
      lastData = millis();
    } else {
      if (!dl.connected()) break;
      if (millis() - lastData > 20000) {
        free(buf);
        otaDownloadFail(dl, "stalled");
        return false;
      }
      delay(2);
    }

    otaProgress = (int)(((int64_t)written * 100) / total);
    if (millis() - lastReport > 3000) {
      lastReport = millis();
      if (mqttClient.connected()) publishHeartbeat("online");
    }
  }
  free(buf);
  dl.stop();

  if (written != total) {
    otaFailAndRestart("incomplete");
    return false;
  }

  unsigned char hash[32];
  mbedtls_sha256_finish(&sha, hash);
  mbedtls_sha256_free(&sha);
  char hex[65];
  for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", hash[i]);
  hex[64] = 0;

  String got = String(hex);
  String want = otaReqSha;
  got.toLowerCase();
  want.toLowerCase();
  if (!constEq(got, want)) {
    otaFailAndRestart("sha_mismatch");
    return false;
  }

  if (!Update.end(true)) {   // also selects the new firmware for the next boot
    otaFailAndRestart("update_end");
    return false;
  }
  return true;
}

void runRemoteOta() {
  Serial.println("Remote update requested: " + otaReqVersion);
  otaState = "downloading";
  otaProgress = 0;
  setOtaMsg("to " + otaReqVersion);
  otaPrepare();
  if (mqttClient.connected()) publishHeartbeat("online");

  if (otaDownloadAndFlash()) {
    otaFinishAndRestart("installed " + otaReqVersion);
  }
}

// Called from onMqttMessage(). Only checks and records the request; otaLoopTick() executes it.
void otaHandleCommand(const byte* payload, unsigned int length) {
  if (length < 10 || length > 900) return;

  String s;
  s.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) s += (char)payload[i];

  String cmd = jsonStr(s, "cmd");
  if (cmd.length() == 0) return;   // not one of ours

  if (!otaKeyConfigured()) {
    Serial.println("Command ignored: OTA_KEY is not set in the sketch");
    return;
  }

  uint32_t seq = jsonNum(s, "seq");
  String version = jsonStr(s, "version");
  String sha = jsonStr(s, "sha256");
  String url = jsonStr(s, "url");
  String sig = jsonStr(s, "sig");

  String signedText = cmd + "|" + String(seq) + "|" + version + "|" + sha + "|" + url;
  char expect[65];
  if (!otaHmacHex(signedText, expect) || !constEq(sig, String(expect))) {
    Serial.println("Command rejected: bad signature");
    return;
  }
  if (seq <= otaGetLastSeq()) {
    Serial.println("Command rejected: old or repeated");
    return;
  }
  otaSetLastSeq(seq);

  if (cmd == "reboot") {
    cmdRebootRequested = true;
  } else if (cmd == "rollback") {
    cmdRollbackRequested = true;
  } else if (cmd == "ota") {
    if (otaInProgress || otaRequested) return;
    if (sha.length() != 64 || version.length() == 0 || url.length() > 250 || !url.startsWith("http://")) {
      Serial.println("Update command invalid");
      return;
    }
    otaReqUrl = url;
    otaReqSha = sha;
    otaReqVersion = version;
    otaReqSize = jsonNum(s, "size");
    otaRequested = true;
  }
}

// Called every pass of loop() and between video frames.
void otaLoopTick() {
  unsigned long now = millis();

  if (otaPageStartRequested) {
    otaPageStartRequested = false;
    startOtaPage();
  }
  if (otaPageActive && !otaInProgress && !identityMissing && (long)(now - otaPageUntilMs) >= 0) {
    stopOtaPage();
  }

  if (cmdRebootRequested) {
    cmdRebootRequested = false;
    Serial.println("Remote restart requested");
    delay(300);
    restartWithCause(CAUSE_REMOTE_REBOOT);
  }
  if (cmdRollbackRequested) {
    cmdRollbackRequested = false;
    Serial.println("Remote rollback requested");
    otaRollbackNow("manual");
  }
  if (otaRequested) {
    otaRequested = false;
    runRemoteOta();
  }

  // New firmware has been healthy long enough -> keep it.
  if (otaPending && now >= OTA_VALIDATE_AFTER_MS && !lanDown && mqttClient.connected()) {
    otaMarkHealthy();
  }
}

// ===================== VIDEO STREAM (raw EthernetServer on port 81) =====================

void startServers() {
  streamServer.begin();
  otaServer.begin();
  Serial.printf("Stream server on port %u, update page port %u (answers only while open)\n",
                (unsigned)STREAM_PORT, (unsigned)OTA_PAGE_PORT);
}

// Everything that must keep running while we sit inside a long video stream:
// health/restart rules, button, update page, MQTT, OTA commands, heartbeat.
// Order matters: mqttClient.loop() runs before otaLoopTick() so a command that just arrived is executed now.
void serviceBackground() {
  healthCheck();
  checkButton();

  unsigned long now = millis();
  if (now - lastPageCheckMs >= 50) {
    lastPageCheckMs = now;
    otaPageService();
  }

  mqttClient.loop();
  otaLoopTick();

  now = millis();
  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    if (mqttClient.connected()) {
      publishHeartbeat("online");
    }
  }
}

// Streams MJPEG to one connected client until it disconnects. The backend is the only client.
// serviceBackground() runs after every frame, so MQTT, OTA commands and the update page
// keep working during a long stream (an update never returns here: it restarts the camera).
void streamToClient(EthernetClient* client) {
  camera_fb_t* fb = NULL;
  size_t jpg_len = 0;
  uint8_t* jpg_buf = NULL;
  char part_buf[64];
  bool send_error = false;
  int off, remaining;

  if (streamBlocked) return;   // firmware update running

  client->println("HTTP/1.1 200 OK");
  client->print("Content-Type: ");
  client->println(STREAM_CONTENT_TYPE);
  client->println("Access-Control-Allow-Origin: *");
  client->println();

  while (client->connected()) {
    if (streamBlocked) break;   // firmware update started: let go of the camera

    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera frame failed");
      return;
    }

    if (fb->format != PIXFORMAT_JPEG) {
      bool ok = frame2jpg(fb, 80, &jpg_buf, &jpg_len);
      esp_camera_fb_return(fb);
      fb = NULL;
      if (!ok) {
        Serial.println("JPEG conversion failed");
        return;
      }
    } else {
      jpg_len = fb->len;
      jpg_buf = fb->buf;
    }

    size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, (unsigned)jpg_len);
    if (client->write((const uint8_t*)part_buf, hlen) != hlen) send_error = true;

    off = 0;
    remaining = (int)jpg_len;
    while (remaining > 0) {
      size_t chunk = remaining > 2048 ? 2048 : (size_t)remaining;
      if (client->write((const uint8_t*)&jpg_buf[off], chunk) != chunk) send_error = true;
      off += chunk;
      remaining -= chunk;
    }

    if (client->write((const uint8_t*)STREAM_BOUNDARY, strlen(STREAM_BOUNDARY)) != strlen(STREAM_BOUNDARY)) {
      send_error = true;
    }

    if (fb) {
      esp_camera_fb_return(fb);
      fb = NULL;
      jpg_buf = NULL;
    } else if (jpg_buf) {
      free(jpg_buf);
      jpg_buf = NULL;
    }

    serviceBackground();

    if (send_error) {
      Serial.println("Stream client disconnected");
      break;
    }
  }
}

void handleStreamServer() {
  EthernetClient client = streamServer.available();
  if (!client) return;

  Serial.println("Stream client connected");

  bool blank_cr = false, blank_lf = false, double_blank = false, single_blank = true;
  bool requestComplete = false;
  unsigned long t0 = millis();

  // Wait (max 5 s) for the end of the HTTP request headers, then stream whatever path was asked for.
  while (client.connected() && millis() - t0 < 5000) {
    wdtFeed();
    if (client.available()) {
      char c = client.read();

      if (c == '\n') {
        if (blank_lf) { double_blank = true; single_blank = true; }
        if (blank_cr) { if (single_blank) double_blank = true; single_blank = true; }
        blank_lf = true;
        blank_cr = false;
      } else if (c == '\r') {
        if (blank_cr) { double_blank = true; single_blank = true; }
        blank_lf = false;
        blank_cr = true;
      } else {
        blank_lf = false;
        blank_cr = false;
        single_blank = false;
        double_blank = false;
      }

      if (double_blank) {
        requestComplete = true;
        break;
      }
    } else {
      delay(1);
    }
  }

  if (requestComplete) {
    streamToClient(&client);
  }

  delay(10);
  client.stop();
  Serial.println("Stream client closed");
}

// ===================== SETUP / LOOP =====================

void setup() {
  Serial.begin(115200);
  delay(1000);

  loadPreviousBootInfo();
  otaBootCheck();   // may switch back to the previous firmware if an update turned out bad
  loadIdentity();   // camera ID / name / password come from flash, not from the firmware file

  Serial.println();
  Serial.println("=== SurvCam LAN camera (W5500) - stream + heartbeat + self-healing + OTA ===");
  Serial.printf("Reset reason: %s (%d), previous restart cause: %s\n",
                resetReasonStr(bootResetReason), (int)bootResetReason,
                restartCauseStr(prevRestartCause));

  pinMode(OTA_BUTTON_PIN, INPUT_PULLUP);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  // Per-camera random jitter on the MQTT restart threshold.
  mqttLostRestartMs = MQTT_LOST_RESTART_MS + random(0, RESTART_JITTER_MS);

  connectEthernet();   // bounded: reboots itself if the W5500 / link / DHCP fails

  if (!initCamera()) {
    Serial.println("Restarting because camera failed");
    delay(3000);
    restartWithCause(CAUSE_CAMERA_INIT_FAIL);
  }

  startServers();

  lastMqttOkMs = millis();

  // Enable the watchdog before the (potentially long) MQTT connect loop;
  // connectMQTT() feeds it through healthCheck().
  setupWatchdog();

  if (identityMissing) {
    Serial.printf("NO CAMERA ID STORED. Open http://%s:%u/identity and enter the OTA key plus this camera's ID, name and password.\n",
                  Ethernet.localIP().toString().c_str(), (unsigned)OTA_PAGE_PORT);
    startOtaPage();
  } else {
    connectMQTT();
  }

  lastMqttOkMs = millis();
}

void loop() {
  serviceBackground();   // watchdog, LAN/MQTT restart rules, button, update page, MQTT, OTA, heartbeat

  if (identityMissing) {
    static unsigned long lastHintBlinkMs = 0;
    if (millis() - lastHintBlinkMs > 3000) {   // slow flash blink = "this camera needs its identity"
      lastHintBlinkMs = millis();
      blinkFlashOnce();
    }
  }

  if (!identityMissing && !lanDown && !mqttClient.connected()) {
    connectMQTT();
  }

  handleStreamServer();   // blocks only while the backend is streaming; serviceBackground() runs inside it

  delay(10);
}
