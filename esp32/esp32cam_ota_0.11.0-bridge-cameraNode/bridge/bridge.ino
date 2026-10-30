// ===================== SURVCAM BRIDGE (ESP32 + UART link to the camera board) =====================
// Runs on a normal ESP32 dev board (e.g. ESP32 DevKit). It owns the WiFi, MQTT, updates and the
// camera's identity; the video comes from the camera board over a UART (see survlink.h).
// Arduino IDE: board "ESP32 Dev Module", partition scheme "Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)".
// survlink.h must be in the same folder as this file.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiManager.h>          // tzapu/WiFiManager
#include <PubSubClient.h>
#include "survlink.h"
#include "esp_http_server.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include <Update.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"
#include "mbedtls/md.h"

// ===================== WIFI SETTINGS =====================
// WiFiManager stores and auto-connects to the last configured WiFi network
// from flash (NVS).
String WIFI_AP_NAME = "ESP32CAM-setup";   // Name of the config-portal AP (becomes ESP32CAM-<camera id>)

// ===================== MQTT SERVER =====================
const char* MQTT_HOST = "YOUR_SERVER_LAN_IP";
const uint16_t MQTT_PORT = 1883;

// ===================== CAMERA IDENTITY =====================
// ONE firmware file now works for EVERY camera: the identity below is kept in the camera's
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
const char* FW_VERSION = "0.1.0-bridge";

// ===================== TIMING =====================
const unsigned long HEARTBEAT_INTERVAL_MS = 5000;

// ===================== SELF-HEALING SETTINGS =====================
// Boot-time WiFi connect: saved network is tried for WIFI_CONNECT_TIMEOUT_S,
// then the config portal stays open for WIFI_PORTAL_TIMEOUT_S, then we reboot.
// 30 + 60 = ~90 seconds per boot attempt.
const unsigned long WIFI_CONNECT_TIMEOUT_S        = 30;
const unsigned long WIFI_PORTAL_TIMEOUT_S         = 60;
// If no credentials are saved yet (fresh device) give more time to configure.
const unsigned long WIFI_PORTAL_TIMEOUT_NOCREDS_S = 300;

// While running: reboot if WiFi has been down this long.
const unsigned long WIFI_LOST_RESTART_MS   = 60000;    // 60 s
// While running: reboot if WiFi is up but MQTT has been down this long.
const unsigned long MQTT_LOST_RESTART_MS   = 180000;   // 3 min
// Random extra delay added to the thresholds above, so that all cameras on
// one AP don't reboot and hammer the AP/DHCP at the same second.
const unsigned long RESTART_JITTER_MS      = 20000;    // 0..20 s
// While WiFi is down, ask the driver to reconnect this often.
const unsigned long WIFI_RECONNECT_RETRY_MS = 15000;

// Task watchdog: reboots if loop() (or connectMQTT) stops running.
const uint32_t WDT_TIMEOUT_MS = 60000;

// ===================== OTA (FIRMWARE UPDATE) SETTINGS =====================
// Shared secret with the backend (ota_key.txt or SURVCAM_OTA_KEY on the server).
// Must be 16+ characters. Make one with:  openssl rand -hex 24
// CHANGE THIS before flashing - updates stay disabled while it is the placeholder.
const char* OTA_KEY = "CHANGE-ME-to-a-long-random-key";

const unsigned long OTA_PAGE_WINDOW_MS    = 600000;   // double-press opens the upload page for 10 min
const uint16_t      OTA_PAGE_PORT         = 8080;     // http://<camera-ip>:8080/update
const unsigned long OTA_VALIDATE_AFTER_MS = 120000;   // new firmware must stay healthy this long...
const uint8_t       OTA_MAX_BOOT_TRIES    = 3;        // ...or it is rolled back after this many boots

// Double-press = two short taps; the 2nd press must start within DOUBLE_TAP_GAP_MS of the 1st release.
const unsigned long TAP_MIN_MS            = 30;       // ignore contact bounce
const unsigned long TAP_MAX_MS            = 400;      // a longer press is not a tap
const unsigned long DOUBLE_TAP_GAP_MS     = 500;

// ===================== WIFI CONFIG BUTTON & INDICATOR =====================
// GPIO0 is the BOOT button on a DevKit. It is a boot-strapping pin (low at reset = download
// mode), so don't hold it while powering up or resetting; pressing it while running is fine.
// You can also wire an external button between GPIO0 and GND.
#define WIFI_CONFIG_BUTTON_PIN    0     // the BOOT button on a DevKit (or wire a button between this pin and GND)
#define FLASH_LED_PIN             2     // the blue LED on most DevKits
const unsigned long WIFI_CONFIG_HOLD_MS = 5000;           // Hold time to trigger AP mode
const unsigned long WIFI_CONFIG_PORTAL_TIMEOUT_S = 180;   // Auto-exit portal after 3 min idle

// ===================== UART LINK TO THE CAMERA BOARD =====================
//   bridge GPIO16 (RX2)  <-  camera board GPIO14 (TX)
//   bridge GPIO17 (TX2)  ->  camera board GPIO13 (RX)
//   GND to GND, and 5V for both boards from the same supply
#define LINK_RX_PIN 16
#define LINK_TX_PIN 17
const uint32_t LINK_BAUD = 2000000;        // MUST match the camera board
const uint32_t MAX_FRAME_BYTES = 40000;    // size of each of the two frame buffers (the camera board compresses harder if a frame is bigger)
const uint32_t FRAME_TIMEOUT_MS = 800;     // give up on a frame after this long

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
WiFiManager wifiManager;   // single global instance, reused for auto-connect and manual portal

httpd_handle_t stream_httpd = NULL;

unsigned long lastHeartbeatMs = 0;

// Connectivity health tracking
unsigned long lastWifiOkMs = 0;
unsigned long lastMqttOkMs = 0;
unsigned long lastWifiReconnectTryMs = 0;
unsigned long wifiLostRestartMs = WIFI_LOST_RESTART_MS;
unsigned long mqttLostRestartMs = MQTT_LOST_RESTART_MS;

// Watchdog state
bool wdtEnabled = false;

// Button hold-tracking state
bool wifiConfigButtonActive = false;
unsigned long wifiConfigButtonPressStartMs = 0;
bool wifiConfigTriggered = false; // prevents re-trigger while button is still held down
bool tapPending = false;          // one short tap seen, waiting for the second
unsigned long lastTapReleaseMs = 0;

// ===================== RESTART CAUSES / DIAGNOSTIC STATE =====================
enum RestartCause : uint8_t {
  CAUSE_NONE = 0,
  CAUSE_WIFI_LOST,
  CAUSE_MQTT_LOST,
  CAUSE_WIFI_CONNECT_TIMEOUT,
  CAUSE_CAMERA_INIT_FAIL,
  CAUSE_OTA,
  CAUSE_OTA_FAIL,
  CAUSE_REMOTE_REBOOT,
  CAUSE_ROLLBACK
};

// Survive a software restart (RTC memory is not cleared by ESP.restart())
#define RTC_MAGIC 0xC0FFEE42UL
RTC_NOINIT_ATTR uint32_t rtcMagic;
RTC_NOINIT_ATTR uint8_t  rtcRestartCause;
RTC_NOINIT_ATTR uint8_t  rtcLastDiscReason;

esp_reset_reason_t bootResetReason = ESP_RST_UNKNOWN;
uint8_t prevRestartCause = CAUSE_NONE;
uint8_t prevDiscReason = 0;

volatile bool restartingNow = false;
volatile uint8_t lastDiscReason = 0;
volatile uint32_t discCount = 0;

// Streaming / OTA shared state
volatile bool streamBlocked = false;      // true while a firmware update runs
volatile bool otaInProgress = false;
volatile bool otaPageStartRequested = false;
volatile bool otaRequested = false;
volatile bool cmdRebootRequested = false;
volatile bool cmdRollbackRequested = false;
bool otaPending = false;                  // new firmware waiting to be verified healthy
volatile int otaProgress = 0;
const char* otaState = "idle";
char otaMsg[64] = "";

bool otaPageActive = false;
unsigned long otaPageUntilMs = 0;
httpd_handle_t ota_httpd = NULL;

String otaReqUrl = "";
String otaReqSha = "";
String otaReqVersion = "";
uint32_t otaReqSize = 0;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// ===================== HELPERS: TOPICS / NAMES =====================

String heartbeatTopic() {
  return String("survcam/cameras/") + CAMERA_ID + "/heartbeat";
}

String commandTopic() {
  return String("survcam/cameras/") + CAMERA_ID + "/cmd";
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
    case CAUSE_WIFI_LOST:            return "wifi_lost";
    case CAUSE_MQTT_LOST:            return "mqtt_lost";
    case CAUSE_WIFI_CONNECT_TIMEOUT: return "wifi_connect_timeout";
    case CAUSE_CAMERA_INIT_FAIL:     return "camera_init_fail";
    case CAUSE_OTA:                  return "ota_update";
    case CAUSE_OTA_FAIL:             return "ota_failed";
    case CAUSE_REMOTE_REBOOT:        return "remote_reboot";
    case CAUSE_ROLLBACK:             return "rollback";
    default:                         return "none";
  }
}

// ===================== RESTART / WATCHDOG HELPERS =====================

void restartWithCause(uint8_t cause) {
  restartingNow = true;   // the WiFi "left" event caused by this restart must not be recorded
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
    rtcLastDiscReason = 0;
  }

  prevRestartCause = rtcRestartCause;
  prevDiscReason = rtcLastDiscReason;

  // Clear so a later reset that we don't tag (e.g. watchdog, panic)
  // doesn't inherit a stale cause.
  rtcRestartCause = CAUSE_NONE;
  rtcLastDiscReason = 0;
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

// Used around long, intentionally blocking operations (config portal).
void wdtPause() {
  if (wdtEnabled) esp_task_wdt_delete(NULL);
}

void wdtResume() {
  if (wdtEnabled) {
    esp_task_wdt_add(NULL);
    esp_task_wdt_reset();
  }
}

// ===================== LINK TO THE CAMERA BOARD =====================
// A background task keeps asking the camera board for the newest frame and holds it in one of
// two buffers. The video server (port 81) sends whichever frame is newest. Nothing queues up,
// so slow WiFi can never make the picture fall behind: the bridge just asks less often.

// ---- video settings, kept in flash and edited on the setup page ----
Preferences videoPrefs;
uint8_t videoProfile = LINK_PROFILE_VGA;
uint8_t videoQuality = 14;       // lower number = sharper picture and bigger frames
uint8_t videoMirror = 0;
uint8_t videoFlip = 0;

uint8_t clampU8(int v, int lo, int hi) {
  if (v < lo) return (uint8_t)lo;
  if (v > hi) return (uint8_t)hi;
  return (uint8_t)v;
}

void loadVideoSettings() {
  videoPrefs.begin("video", false);
  videoProfile = clampU8(videoPrefs.getUChar("prof", LINK_PROFILE_VGA), 0, LINK_PROFILE_SVGA);
  videoQuality = clampU8(videoPrefs.getUChar("q", 14), 8, 40);
  videoMirror = videoPrefs.getUChar("hm", 0) ? 1 : 0;
  videoFlip = videoPrefs.getUChar("vf", 0) ? 1 : 0;
  videoPrefs.end();
}

void saveVideoSettings() {
  videoPrefs.begin("video", false);
  videoPrefs.putUChar("prof", videoProfile);
  videoPrefs.putUChar("q", videoQuality);
  videoPrefs.putUChar("hm", videoMirror);
  videoPrefs.putUChar("vf", videoFlip);
  videoPrefs.end();
}

String videoOption(int value, const char* label) {
  String o = "<option value=";
  o += String(value);
  if (videoProfile == value) o += " selected";
  o += ">";
  o += label;
  o += "</option>";
  return o;
}

// ---- frame buffers shared between the link task and the video server ----
uint8_t* frameBuf[2] = {NULL, NULL};   // each holds: seq (2 bytes) + JPEG
uint32_t frameLen[2] = {0, 0};         // JPEG length, without the 2 seq bytes
volatile int latestIdx = -1;           // newest complete frame (-1 = none)
volatile int sendingIdx = -1;          // frame the video server is sending right now
volatile uint32_t latestCounter = 0;   // goes up with every new frame
volatile uint32_t takenCounter = 0;    // the newest frame the video server has picked up
portMUX_TYPE frameMux = portMUX_INITIALIZER_UNLOCKED;

volatile bool linkOk = false;          // camera board answers
volatile bool linkPaused = false;      // true while a firmware update runs
volatile bool linkTaskIdle = false;
volatile int streamClients = 0;
volatile uint32_t linkFrames = 0, linkErrors = 0, linkTimeouts = 0, camUptimeMs = 0;
float linkFps = 0;
TaskHandle_t linkTaskHandle = NULL;

// Picks a buffer to receive the next frame into: never the one being sent.
int pickWriteBuffer() {
  int w;
  portENTER_CRITICAL(&frameMux);
  if (sendingIdx == 0) w = 1;
  else if (sendingIdx == 1) w = 0;
  else w = (latestIdx == 0) ? 1 : 0;
  if (latestIdx == w) latestIdx = -1;   // about to overwrite it
  portEXIT_CRITICAL(&frameMux);
  return w;
}

void publishFrame(int w, uint32_t jpegLen) {
  portENTER_CRITICAL(&frameMux);
  frameLen[w] = jpegLen;
  latestIdx = w;
  latestCounter = latestCounter + 1;
  portEXIT_CRITICAL(&frameMux);
}

// Sends the video settings and waits for the camera board to confirm.
bool linkSendConfig() {
  uint8_t p[8] = { videoProfile, videoQuality, videoMirror, videoFlip, 0, 0, 0, 0 };
  linkPutU32(p + 4, MAX_FRAME_BYTES);
  linkFlush(Serial2);
  linkWriteSimple(Serial2, LINK_T_CONFIG, p, 8);

  uint8_t rb[48];
  uint32_t n = 0;
  return linkReadPacket(Serial2, rb, sizeof(rb), &n, 600) == LINK_T_CONFIG_ACK;
}

bool linkPing() {
  static uint32_t nonce = 1;
  uint8_t p[4];
  linkPutU32(p, nonce++);
  linkFlush(Serial2);
  linkWriteSimple(Serial2, LINK_T_PING, p, 4);

  uint8_t rb[32];
  uint32_t n = 0;
  if (linkReadPacket(Serial2, rb, sizeof(rb), &n, 400) != LINK_T_PONG || n < 13) return false;
  camUptimeMs = linkGetU32(rb + 4);
  return true;
}

void linkTask(void* arg) {
  uint16_t seq = 1;
  bool configured = false;
  uint8_t fails = 0;
  unsigned long lastPing = 0;
  unsigned long fpsStart = millis();
  uint32_t fpsFrames = 0;

  for (;;) {
    if (linkPaused) {
      linkTaskIdle = true;
      delay(50);
      continue;
    }
    linkTaskIdle = false;
    unsigned long now = millis();

    // (Re)send the settings until the camera board confirms. This also re-syncs the two
    // boards after either one restarted.
    if (!configured) {
      configured = linkSendConfig();
      if (!configured) {
        linkOk = false;
        delay(500);
        continue;
      }
      linkOk = true;
      fails = 0;
    }

    // Nobody is watching: just check now and then that the camera board is alive.
    if (streamClients <= 0) {
      linkFps = 0;
      fpsFrames = 0;
      fpsStart = now;
      if (now - lastPing > 2000) {
        lastPing = now;
        if (linkPing()) {
          linkOk = true;
          fails = 0;
        } else if (++fails >= 3) {
          linkOk = false;
          configured = false;
        }
      }
      delay(20);
      continue;
    }

    // A newer frame is already waiting for the video server: don't fetch another yet.
    if (latestIdx >= 0 && latestCounter != takenCounter) {
      delay(1);
      continue;
    }

    int w = pickWriteBuffer();
    uint8_t req[2] = { (uint8_t)(seq & 0xFF), (uint8_t)(seq >> 8) };
    linkWriteSimple(Serial2, LINK_T_FRAME_REQ, req, 2);

    uint32_t plen = 0;
    int type = linkReadPacket(Serial2, frameBuf[w], MAX_FRAME_BYTES + 2, &plen, FRAME_TIMEOUT_MS);

    if (type == LINK_T_FRAME && plen > 4) {
      uint16_t rseq = (uint16_t)(frameBuf[w][0] | (frameBuf[w][1] << 8));
      if (rseq == seq) {
        publishFrame(w, plen - 2);
        linkFrames = linkFrames + 1;
        fpsFrames++;
        fails = 0;
        linkOk = true;
        seq++;
      } else {
        linkErrors = linkErrors + 1;       // a late answer to an older request
      }
    } else if (type == LINK_T_TOOBIG) {
      // the camera board compressed harder; just ask again
    } else if (type == LINK_T_ERROR) {
      linkErrors = linkErrors + 1;         // camera not ready yet
      delay(100);
    } else if (type == 0) {
      linkTimeouts = linkTimeouts + 1;
      linkFlush(Serial2);
      if (++fails >= 5) {
        linkOk = false;
        configured = false;
      }
    } else {
      linkErrors = linkErrors + 1;         // damaged or oversized packet
      linkFlush(Serial2);
    }

    if (now - fpsStart >= 10000) {
      linkFps = (fpsFrames * 1000.0f) / (float)(now - fpsStart);
      fpsFrames = 0;
      fpsStart = now;
    }
  }
}

void initLink() {
  frameBuf[0] = (uint8_t*)malloc(MAX_FRAME_BYTES + 16);
  frameBuf[1] = (uint8_t*)malloc(MAX_FRAME_BYTES + 16);
  if (!frameBuf[0] || !frameBuf[1]) {
    Serial.println("Not enough memory for the frame buffers -> restarting");
    delay(1000);
    restartWithCause(CAUSE_NONE);
  }
  Serial.printf("Frame buffers allocated, free heap %u\n", ESP.getFreeHeap());

  Serial2.setRxBufferSize(16384);
  Serial2.begin(LINK_BAUD, SERIAL_8N1, LINK_RX_PIN, LINK_TX_PIN);
  xTaskCreatePinnedToCore(linkTask, "link", 6144, NULL, 2, &linkTaskHandle, 1);
}

// Used while a firmware update runs: stop talking to the camera board.
void linkStop() {
  linkPaused = true;
  for (int i = 0; i < 40 && !linkTaskIdle; i++) delay(25);
}

static esp_err_t streamHandler(httpd_req_t* req) {
  esp_err_t res = ESP_OK;
  char part_buf[64];

  if (streamBlocked) return ESP_FAIL;   // firmware update running

  res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) {
    return res;
  }
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  // Start from "now": don't send an old frame left over from an earlier viewer.
  uint32_t lastCounter;
  portENTER_CRITICAL(&frameMux);
  lastCounter = latestCounter;
  takenCounter = latestCounter;
  portEXIT_CRITICAL(&frameMux);
  streamClients = streamClients + 1;

  unsigned long lastFrameAt = millis();

  while (true) {
    if (streamBlocked) {      // firmware update started
      res = ESP_FAIL;
      break;
    }

    int idx = -1;
    uint32_t len = 0;
    portENTER_CRITICAL(&frameMux);
    if (latestIdx >= 0 && latestCounter != lastCounter) {
      idx = latestIdx;
      sendingIdx = idx;
      lastCounter = latestCounter;
      takenCounter = latestCounter;
      len = frameLen[idx];
    }
    portEXIT_CRITICAL(&frameMux);

    if (idx < 0) {
      if (millis() - lastFrameAt > 4000) {
        Serial.println("No frames from the camera board - closing stream");
        res = ESP_FAIL;
        break;
      }
      delay(2);
      continue;
    }
    lastFrameAt = millis();

    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) {
      size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, (unsigned)len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char*)(frameBuf[idx] + 2), len);   // skip the 2 seq bytes
    }

    portENTER_CRITICAL(&frameMux);
    sendingIdx = -1;
    portEXIT_CRITICAL(&frameMux);

    if (res != ESP_OK) {
      Serial.println("Stream client disconnected");
      break;
    }
  }

  streamClients = streamClients - 1;
  return res;
}

void startCameraStreamServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  config.ctrl_port = 32769;

  httpd_uri_t stream_uri = {
    .uri = "/stream",
    .method = HTTP_GET,
    .handler = streamHandler,
    .user_ctx = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
    Serial.println("Camera stream server started on port 81");
  } else {
    Serial.println("Failed to start camera stream server");
  }
}

// ===================== WIFI CONFIG BUTTON & FLASH INDICATOR =====================

void blinkFlashOnce() {
  digitalWrite(FLASH_LED_PIN, HIGH);
  delay(200);
  digitalWrite(FLASH_LED_PIN, LOW);
}

// Called automatically by WiFiManager whenever it opens the config AP,
// whether that happens via autoConnect() (no saved/working credentials)
// or via the button-triggered startConfigPortal() below.
void onWifiConfigAPStart(WiFiManager* wm) {
  Serial.print("Entered WiFi AP config mode, AP name: ");
  Serial.println(wm->getConfigPortalSSID());
  blinkFlashOnce();
}

// WiFi disconnect event: remember WHY the AP link dropped.
// (Runs in the WiFi event task, so only touch simple globals here.)
void onWifiDisconnected(arduino_event_id_t event, arduino_event_info_t info) {
  if (restartingNow) return;   // our own restart disconnects WiFi (reason 8): not a real drop
  uint8_t reason = info.wifi_sta_disconnected.reason;
  lastDiscReason = reason;
  rtcLastDiscReason = reason;
  discCount = discCount + 1;
  Serial.printf("WiFi disconnected, reason=%u\n", reason);
}

// Boot-time connect. Bounded: ~30 s on saved network + 60 s portal, then reboot.
void connectWiFi() {
  wifiManager.setConnectTimeout(WIFI_CONNECT_TIMEOUT_S);

  if (wifiManager.getWiFiIsSaved()) {
    wifiManager.setConfigPortalTimeout(WIFI_PORTAL_TIMEOUT_S);
  } else {
    // Nothing saved yet: don't reboot-loop before someone can configure it.
    wifiManager.setConfigPortalTimeout(WIFI_PORTAL_TIMEOUT_NOCREDS_S);
  }

  Serial.println("Connecting to WiFi (WiFiManager autoConnect)...");

  bool connected = wifiManager.autoConnect(WIFI_AP_NAME.c_str());

  if (!connected) {
    Serial.println("WiFi not connected within time limit, restarting...");
    // Random delay so cameras on the same AP don't all retry in lockstep.
    delay(random(0, 10000));
    restartWithCause(CAUSE_WIFI_CONNECT_TIMEOUT);
    return;
  }

  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  Serial.println("WiFi connected");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

// Manually forces the config portal open so the user can pick a new
// WiFi network, without erasing the currently saved credentials unless
// they actually complete the form with new ones.
void enterWifiConfigPortal() {
  Serial.println("Button held 5s -> opening WiFi config portal");

  wdtPause();   // portal blocks for up to 180 s on purpose

  wifiManager.setConfigPortalTimeout(WIFI_CONFIG_PORTAL_TIMEOUT_S);
  bool connected = wifiManager.startConfigPortal(WIFI_AP_NAME.c_str());

  if (connected) {
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    Serial.println("WiFi (re)connected with updated credentials");
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Config portal timed out/closed, keeping previous WiFi state");
    if (WiFi.status() != WL_CONNECTED) {
      // Make sure we're back in plain station mode and trying the saved network.
      WiFi.mode(WIFI_STA);
      WiFi.begin();
    }
  }

  // Portal time must not count as "WiFi/MQTT lost" time.
  unsigned long now = millis();
  lastWifiOkMs = now;
  lastMqttOkMs = now;

  wdtResume();
}

// Non-blocking check of the button; call this frequently, including from
// inside any blocking retry loops (e.g. connectMQTT()), not just loop().
// Returns true only on the call where it actually opened+closed the config
// portal, so callers can bail out and let the rest of the sketch re-sync.
bool checkWifiConfigButton() {
  bool pressed = (digitalRead(WIFI_CONFIG_BUTTON_PIN) == LOW); // pressed = pulled to GND

  if (pressed) {
    if (!wifiConfigButtonActive) {
      wifiConfigButtonActive = true;
      wifiConfigTriggered = false;
      wifiConfigButtonPressStartMs = millis();
    } else if (!wifiConfigTriggered &&
               (millis() - wifiConfigButtonPressStartMs >= WIFI_CONFIG_HOLD_MS)) {
      wifiConfigTriggered = true; // avoid re-triggering while still held
      enterWifiConfigPortal();
      return true;
    }
  } else {
    if (wifiConfigButtonActive) {
      // Button was just released: is this the second tap of a double-press?
      unsigned long now = millis();
      unsigned long heldMs = now - wifiConfigButtonPressStartMs;

      if (!wifiConfigTriggered && heldMs >= TAP_MIN_MS && heldMs <= TAP_MAX_MS) {
        if (tapPending && (wifiConfigButtonPressStartMs - lastTapReleaseMs) <= DOUBLE_TAP_GAP_MS) {
          tapPending = false;
          otaPageStartRequested = true;   // opened from loop()
        } else {
          tapPending = true;
          lastTapReleaseMs = now;
        }
      } else if (heldMs > TAP_MAX_MS || wifiConfigTriggered) {
        tapPending = false;
      }
    }
    wifiConfigButtonActive = false;
    wifiConfigTriggered = false;
  }

  return false;
}

// ===================== HEALTH CHECK (self-healing core) =====================
// Call frequently: from loop() AND from inside any blocking retry loop.
// - feeds the watchdog
// - reboots if WiFi has been down too long
// - reboots if WiFi is up but MQTT has been down too long
// - nudges the WiFi driver to reconnect while WiFi is down
void healthCheck() {
  wdtFeed();

  if (otaInProgress) {   // a download/flash is running: don't treat it as a WiFi/MQTT outage
    unsigned long t = millis();
    lastWifiOkMs = t;
    lastMqttOkMs = t;
    return;
  }

  unsigned long now = millis();
  bool wifiUp = (WiFi.status() == WL_CONNECTED);

  if (wifiUp) {
    lastWifiOkMs = now;
  }

  // MQTT timer only runs while WiFi is up (WiFi-down has its own timer).
  if (!wifiUp || mqttClient.connected() || identityMissing) {
    lastMqttOkMs = now;
  }

  if (!wifiUp) {
    if (now - lastWifiOkMs > wifiLostRestartMs) {
      Serial.println("WiFi lost too long");
      restartWithCause(CAUSE_WIFI_LOST);
    }

    if (now - lastWifiReconnectTryMs >= WIFI_RECONNECT_RETRY_MS) {
      lastWifiReconnectTryMs = now;
      Serial.println("WiFi down -> WiFi.reconnect()");
      WiFi.reconnect();
    }
  } else if (now - lastMqttOkMs > mqttLostRestartMs) {
    Serial.println("MQTT down too long while WiFi is up");
    restartWithCause(CAUSE_MQTT_LOST);
  }
}

// Drop-in replacement for delay(ms) that keeps polling the config button
// and the health check in small increments instead of blocking blind.
void waitWithButtonCheck(unsigned long ms) {
  unsigned long start = millis();

  while (millis() - start < ms) {
    healthCheck();
    if (checkWifiConfigButton()) {
      return; // portal already ran; no point finishing the rest of the wait
    }
    delay(100);
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
  payload += WiFi.localIP().toString();
  payload += "\",";
  payload += "\"stream_port\":81,";
  payload += "\"stream_path\":\"/stream\",";
  payload += "\"rssi\":";
  payload += String(WiFi.RSSI());
  payload += ",";
  payload += "\"uptime_ms\":";
  payload += String(millis());
  payload += ",";
  payload += "\"fw_version\":\"";
  payload += FW_VERSION;
  payload += "\"";

  // ---- diagnostics: why did this camera last reboot / disconnect? ----
  payload += ",\"reset_reason\":";
  payload += String((int)bootResetReason);
  payload += ",\"reset_reason_str\":\"";
  payload += resetReasonStr(bootResetReason);
  payload += "\",\"restart_cause\":\"";
  payload += restartCauseStr(prevRestartCause);   // our own reason for the last reboot
  payload += "\",\"prev_disc_reason\":";
  payload += String((int)prevDiscReason);         // last WiFi disconnect reason BEFORE that reboot
  payload += ",\"disc_reason\":";
  payload += String((int)lastDiscReason);         // last WiFi disconnect reason this boot
  payload += ",\"disc_count\":";
  payload += String((unsigned long)discCount);    // WiFi drops this boot
  payload += ",\"channel\":";
  payload += String(WiFi.channel());
  payload += ",\"bssid\":\"";
  payload += WiFi.BSSIDstr();                     // which AP the camera is on
  payload += "\",\"free_heap\":";
  payload += String(ESP.getFreeHeap());

  // ---- what this device is, and how healthy the link to the camera board is ----
  payload += ",\"hw_kind\":\"bridge\",\"link_ok\":";
  payload += (linkOk ? "1" : "0");
  payload += ",\"link_fps\":";
  payload += String(linkFps, 1);
  payload += ",\"link_errors\":";
  payload += String((unsigned long)linkErrors);
  payload += ",\"link_timeouts\":";
  payload += String((unsigned long)linkTimeouts);
  payload += ",\"cam_uptime_ms\":";
  payload += String((unsigned long)camUptimeMs);

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
    // itself (watchdog feed + WiFi/MQTT restart rules).
    healthCheck();

    // WiFi dropped: go back to loop() instead of spinning here forever.
    if (WiFi.status() != WL_CONNECTED) {
      return;
    }

    // Bail out immediately if the button just triggered the config portal,
    // so control returns to loop() and the rest of the sketch re-syncs.
    if (checkWifiConfigButton()) {
      return;
    }

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
      waitWithButtonCheck(3000); // 3s backoff, but stays responsive to button + health check
    }
  }
}

// ===================== OTA: FIRMWARE UPDATES, REMOTE REBOOT, ROLLBACK =====================
// Three ways in:
//   1. Remote : backend sends a SIGNED "ota" command on survcam/cameras/<id>/cmd; the camera
//               downloads the .bin from the backend, verifies SHA-256 and installs it.
//   2. Local  : double-press the button -> upload page http://<camera-ip>:8080/update for 10 min.
//   3. Cable  : unchanged.
// Safety net: a freshly installed firmware must stay healthy (WiFi + MQTT connected) for
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
  linkStop();             // stop talking to the camera board while updating
}

// fromLoop = true only when called from loop() (MQTT must not be touched from other tasks).
void otaFailAndRestart(const String& why, bool fromLoop) {
  Serial.println("OTA failed: " + why);
  if (Update.isRunning()) Update.abort();
  setOtaMsg("failed:" + why);
  otaState = "failed";
  otaRecordFailure(why);
  if (fromLoop && mqttClient.connected()) {
    publishHeartbeat("online");
    mqttClient.loop();
  }
  delay(500);
  restartWithCause(CAUSE_OTA_FAIL);
}

void otaFinishAndRestart(const String& msg, bool fromLoop) {
  otaMarkInstalled();
  otaState = "installed";
  otaProgress = 100;
  setOtaMsg(msg);
  Serial.println("OTA installed: " + msg);
  if (fromLoop && mqttClient.connected()) {
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
  WIFI_AP_NAME = identityMissing ? String("ESP32CAM-setup") : String("ESP32CAM-") + CAMERA_ID;
}

void saveIdentity(const String& id, const String& name, const String& pw) {
  identPrefs.begin("ident", false);
  identPrefs.putString("id", id);
  identPrefs.putString("name", name);
  identPrefs.putString("pw", pw);
  identPrefs.end();
}

// ---- 2. local upload page (double-press) ----

static esp_err_t otaPageGet(httpd_req_t* req) {
  String page = F("<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
                  "<title>SurvCam setup</title></head>"
                  "<body style='font-family:sans-serif;max-width:480px;margin:20px auto;padding:0 12px'>"
                  "<h3>SurvCam camera setup</h3><p>Camera: ");
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
  page += F("'><h4>Video (applied by the camera board)</h4>Size: <select id=v>");
  page += videoOption(LINK_PROFILE_QVGA, "QVGA 320x240");
  page += videoOption(LINK_PROFILE_CIF, "CIF 400x296");
  page += videoOption(LINK_PROFILE_VGA, "VGA 640x480");
  page += videoOption(LINK_PROFILE_SVGA, "SVGA 800x600");
  page += F("</select><br><br>JPEG quality number (8 = sharpest and biggest, 40 = smallest): "
            "<input id=q type=number min=8 max=40 style='width:70px' value='");
  page += String((int)videoQuality);
  page += F("'><br><br><label><input type=checkbox id=hm");
  page += (videoMirror ? " checked" : "");
  page += F("> Mirror left-right</label><br><label><input type=checkbox id=vf");
  page += (videoFlip ? " checked" : "");
  page += F("> Flip upside-down</label><br><br>"
            "<button onclick='sv()' style='padding:10px 20px'>Save and restart</button>"
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
            "x.send(JSON.stringify({id:document.getElementById('i').value,name:document.getElementById('n').value,pw:document.getElementById('p').value,"
            "prof:document.getElementById('v').value,q:document.getElementById('q').value,"
            "hm:document.getElementById('hm').checked?'1':'0',vf:document.getElementById('vf').checked?'1':'0'}))}"
            "</script></body></html>");
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, page.c_str(), page.length());
}

static esp_err_t otaIdentityPost(httpd_req_t* req) {
  if (!otaPageActive) {
    httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "Setup page is closed - double-press the button again");
    return ESP_FAIL;
  }

  char key[100] = {0};
  if (httpd_req_get_hdr_value_str(req, "X-OTA-Key", key, sizeof(key)) != ESP_OK ||
      !otaKeyConfigured() || !constEq(String(key), String(OTA_KEY))) {
    httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Wrong OTA key");
    return ESP_FAIL;
  }

  size_t total = req->content_len;
  if (total == 0 || total > 400) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Bad request size");
    return ESP_FAIL;
  }

  char body[401];
  size_t got = 0;
  int idle = 0;
  while (got < total) {
    int r = httpd_req_recv(req, body + got, total - got);
    if (r == HTTPD_SOCK_ERR_TIMEOUT) {
      if (++idle > 5) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Request timed out");
        return ESP_FAIL;
      }
      continue;
    }
    if (r <= 0) {
      httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Request aborted");
      return ESP_FAIL;
    }
    got += r;
  }
  body[got] = 0;

  String s = String(body);
  String id = cleanIdentityText(jsonStr(s, "id"), 24);
  String name = cleanIdentityText(jsonStr(s, "name"), 40);
  String pw = cleanIdentityText(jsonStr(s, "pw"), 24);

  if (!validCameraId(id)) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Camera ID must be 3-24 letters, digits, - or _");
    return ESP_FAIL;
  }
  if (name.length() == 0) name = id;

  saveIdentity(id, name, pw);

  // video settings travel in the same form (they are optional: keep the old value if missing)
  String sp = jsonStr(s, "prof");
  String sq = jsonStr(s, "q");
  String shm = jsonStr(s, "hm");
  String svf = jsonStr(s, "vf");
  if (sp.length() > 0) videoProfile = clampU8(sp.toInt(), 0, LINK_PROFILE_SVGA);
  if (sq.length() > 0) videoQuality = clampU8(sq.toInt(), 8, 40);
  if (shm.length() > 0) videoMirror = (shm == "1") ? 1 : 0;
  if (svf.length() > 0) videoFlip = (svf == "1") ? 1 : 0;
  saveVideoSettings();
  httpd_resp_sendstr(req, "Saved. The camera is restarting and will come back as this camera.");
  delay(500);
  restartWithCause(CAUSE_NONE);
  return ESP_OK;
}

static esp_err_t otaPagePost(httpd_req_t* req) {
  if (!otaPageActive || ((long)(millis() - otaPageUntilMs) >= 0 && !identityMissing)) {
    httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "Update page is closed - double-press the button again");
    return ESP_FAIL;
  }
  if (otaInProgress) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "An update is already running");
    return ESP_FAIL;
  }

  char key[100] = {0};
  if (httpd_req_get_hdr_value_str(req, "X-OTA-Key", key, sizeof(key)) != ESP_OK ||
      !otaKeyConfigured() || !constEq(String(key), String(OTA_KEY))) {
    httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Wrong OTA key");
    return ESP_FAIL;
  }

  size_t total = req->content_len;
  const esp_partition_t* target = esp_ota_get_next_update_partition(NULL);
  if (total < 100000 || target == NULL || total > target->size) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "File size is not valid for this camera");
    return ESP_FAIL;
  }

  otaState = "uploading";
  otaProgress = 0;
  setOtaMsg("browser upload");
  otaPrepare();

  if (!Update.begin(total)) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Could not start the update");
    otaFailAndRestart("update_begin", false);
    return ESP_FAIL;
  }

  uint8_t* buf = (uint8_t*)malloc(2048);
  if (!buf) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    otaFailAndRestart("no_memory", false);
    return ESP_FAIL;
  }

  size_t remaining = total;
  int idle = 0;
  while (remaining > 0) {
    int r = httpd_req_recv(req, (char*)buf, remaining < 2048 ? remaining : 2048);
    if (r == HTTPD_SOCK_ERR_TIMEOUT) {
      if (++idle > 10) {
        free(buf);
        otaFailAndRestart("upload_stalled", false);
        return ESP_FAIL;
      }
      continue;
    }
    if (r <= 0) {
      free(buf);
      otaFailAndRestart("upload_aborted", false);
      return ESP_FAIL;
    }
    idle = 0;
    if (Update.write(buf, r) != (size_t)r) {
      free(buf);
      httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Flash write failed (is this a camera .bin?)");
      otaFailAndRestart("write_failed", false);
      return ESP_FAIL;
    }
    remaining -= r;
    otaProgress = (int)(((total - remaining) * 100ULL) / total);
  }
  free(buf);

  if (!Update.end(true)) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Image check failed");
    otaFailAndRestart("update_end", false);
    return ESP_FAIL;
  }

  httpd_resp_sendstr(req, "Update installed. The camera is restarting; the app will show the new version in a minute or two.");
  otaFinishAndRestart("installed (browser)", false);
  return ESP_OK;
}

void startOtaPage() {
  if (!otaKeyConfigured()) {
    Serial.println("Update page not started: OTA_KEY is not set in the sketch");
    return;
  }

  otaPageUntilMs = millis() + OTA_PAGE_WINDOW_MS;   // a second double-press extends the time
  if (otaPageActive) return;

  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = OTA_PAGE_PORT;
  cfg.ctrl_port = 32770;          // the video server uses 32769
  cfg.stack_size = 8192;
  cfg.recv_wait_timeout = 10;
  cfg.send_wait_timeout = 10;

  httpd_uri_t getUri = {
    .uri = "/update",
    .method = HTTP_GET,
    .handler = otaPageGet,
    .user_ctx = NULL
  };
  httpd_uri_t postUri = {
    .uri = "/update",
    .method = HTTP_POST,
    .handler = otaPagePost,
    .user_ctx = NULL
  };

  httpd_uri_t identGetUri = {
    .uri = "/identity",
    .method = HTTP_GET,
    .handler = otaPageGet,
    .user_ctx = NULL
  };
  httpd_uri_t identPostUri = {
    .uri = "/identity",
    .method = HTTP_POST,
    .handler = otaIdentityPost,
    .user_ctx = NULL
  };

  if (httpd_start(&ota_httpd, &cfg) == ESP_OK) {
    httpd_register_uri_handler(ota_httpd, &getUri);
    httpd_register_uri_handler(ota_httpd, &postUri);
    httpd_register_uri_handler(ota_httpd, &identGetUri);
    httpd_register_uri_handler(ota_httpd, &identPostUri);
    otaPageActive = true;
    Serial.printf("Update page open for %lu min: http://%s:%u/update\n",
                  OTA_PAGE_WINDOW_MS / 60000UL, WiFi.localIP().toString().c_str(), (unsigned)OTA_PAGE_PORT);
    for (int i = 0; i < 3; i++) {   // three quick flashes = page is open
      blinkFlashOnce();
      delay(150);
    }
  } else {
    Serial.println("Failed to start the update page");
  }
}

void stopOtaPage() {
  if (ota_httpd) {
    httpd_stop(ota_httpd);
    ota_httpd = NULL;
  }
  otaPageActive = false;
  Serial.println("Update page closed");
}

// ---- 1. remote update: download from the backend ----

bool otaDownloadAndFlash() {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(10000);
  http.setTimeout(15000);

  if (!http.begin(client, otaReqUrl)) {
    otaFailAndRestart("http_begin", true);
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    http.end();
    otaFailAndRestart("http_" + String(code), true);
    return false;
  }

  int total = http.getSize();
  const esp_partition_t* target = esp_ota_get_next_update_partition(NULL);
  if (total <= 0 || (otaReqSize != 0 && (uint32_t)total != otaReqSize)) {
    http.end();
    otaFailAndRestart("size_mismatch", true);
    return false;
  }
  if (target == NULL || (uint32_t)total > target->size) {
    http.end();
    otaFailAndRestart("too_big", true);
    return false;
  }
  if (!Update.begin(total)) {
    http.end();
    otaFailAndRestart("update_begin", true);
    return false;
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);

  uint8_t* buf = (uint8_t*)malloc(1460);
  if (!buf) {
    http.end();
    otaFailAndRestart("no_memory", true);
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  int written = 0;
  unsigned long lastData = millis();
  unsigned long lastReport = 0;

  while (written < total) {
    wdtFeed();
    mqttClient.loop();    // keep the MQTT connection alive during the download

    int avail = stream->available();
    if (avail > 0) {
      int want = avail < 1460 ? avail : 1460;
      int n = stream->readBytes(buf, want);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) {
        free(buf);
        http.end();
        otaFailAndRestart("write_failed", true);
        return false;
      }
      mbedtls_sha256_update(&sha, buf, n);
      written += n;
      lastData = millis();
    } else {
      if (!stream->connected()) break;
      if (millis() - lastData > 20000) {
        free(buf);
        http.end();
        otaFailAndRestart("stalled", true);
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
  http.end();

  if (written != total) {
    otaFailAndRestart("incomplete", true);
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
    otaFailAndRestart("sha_mismatch", true);
    return false;
  }

  if (!Update.end(true)) {   // also selects the new firmware for the next boot
    otaFailAndRestart("update_end", true);
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
    otaFinishAndRestart("installed " + otaReqVersion, true);
  }
}

// Called from onMqttMessage(). Only checks and records the request; loop() executes it.
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

// Called every pass of loop().
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
  if (otaPending && now >= OTA_VALIDATE_AFTER_MS &&
      WiFi.status() == WL_CONNECTED && mqttClient.connected()) {
    otaMarkHealthy();
  }
}

// ===================== SETUP / LOOP =====================

void setup() {
  Serial.begin(115200);
  delay(1000);

  loadPreviousBootInfo();
  otaBootCheck();   // may switch back to the previous firmware if an update turned out bad
  loadIdentity();   // camera ID / name / password come from flash, not from the firmware file
  loadVideoSettings();   // picture size, quality, mirror and flip also live in flash

  Serial.println();
  Serial.println("=== SurvCam BRIDGE: WiFi + MQTT + updates, video from the camera board over UART ===");
  Serial.printf("Reset reason: %s (%d), previous restart cause: %s, previous WiFi disc reason: %u\n",
                resetReasonStr(bootResetReason), (int)bootResetReason,
                restartCauseStr(prevRestartCause), prevDiscReason);

  pinMode(WIFI_CONFIG_BUTTON_PIN, INPUT_PULLUP);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  // Per-camera random jitter on the restart thresholds.
  wifiLostRestartMs = WIFI_LOST_RESTART_MS + random(0, RESTART_JITTER_MS);
  mqttLostRestartMs = MQTT_LOST_RESTART_MS + random(0, RESTART_JITTER_MS);

  // Must be registered before connecting so the first drops are captured.
  WiFi.onEvent(onWifiDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

  wifiManager.setAPCallback(onWifiConfigAPStart);

  connectWiFi();   // bounded: reboots itself if it can't connect

  initLink();   // starts the UART and the background task that fetches frames

  startCameraStreamServer();

  unsigned long now = millis();
  lastWifiOkMs = now;
  lastMqttOkMs = now;

  // Enable the watchdog before the (potentially long) MQTT connect loop;
  // connectMQTT() feeds it through healthCheck().
  setupWatchdog();

  if (identityMissing) {
    Serial.printf("NO CAMERA ID STORED. Open http://%s:%u/identity and enter the OTA key plus this camera's ID, name and password.\n",
                  WiFi.localIP().toString().c_str(), (unsigned)OTA_PAGE_PORT);
    startOtaPage();
  } else {
    connectMQTT();
  }

  now = millis();
  lastWifiOkMs = now;
  lastMqttOkMs = now;
}

void loop() {
  checkWifiConfigButton();

  healthCheck();   // feeds watchdog + WiFi/MQTT restart rules

  otaLoopTick();   // updates, remote reboot/rollback, upload page, healthy-check

  if (identityMissing) {
    static unsigned long lastHintBlinkMs = 0;
    if (millis() - lastHintBlinkMs > 3000) {   // slow flash blink = "this camera needs its identity"
      lastHintBlinkMs = millis();
      blinkFlashOnce();
    }
  }

  if (!identityMissing && WiFi.status() == WL_CONNECTED && !mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();

  unsigned long now = millis();

  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    if (mqttClient.connected()) {
      publishHeartbeat("online");
    }
  }

  delay(10);
}
