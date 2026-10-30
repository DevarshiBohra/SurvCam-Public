// ===================== SURVCAM CAMERA NODE =====================
// Runs on the AI Thinker ESP32-CAM whose own WiFi is bad or damaged.
// It does ONE job: capture JPEG frames and hand them to the bridge board over a UART.
// WiFi and Bluetooth stay switched off (less heat, no radio noise next to the sensor).
//
// Wiring (keep the wires short, under ~15 cm, and twist the GND wire with them):
//   camera GPIO14 (TX)  ->  bridge GPIO16 (RX2)
//   camera GPIO13 (RX)  <-  bridge GPIO17 (TX2)
//   camera GND          --  bridge GND
//   5V for both boards from the same supply (common ground is essential)
//
// Arduino IDE: board "AI Thinker ESP32-CAM". The partition scheme does not matter here
// (this board is updated by cable). Flash it with the bridge disconnected.
//
// survlink.h must be in the same folder as this file.

#include <Arduino.h>
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_task_wdt.h"
#include "survlink.h"

const char* NODE_FW_TAG = "camnode-0.1.0";

#define LINK_RX_PIN 13                    // receives from the bridge
#define LINK_TX_PIN 14                    // sends to the bridge
const uint32_t LINK_BAUD = 2000000;       // MUST match the bridge. Lower it (e.g. 1000000) if the wires are long.

// ---- AI Thinker ESP32-CAM pin map ----
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
#define FLASH_LED_PIN      4

// Video settings. These are only the starting values: the bridge sends its own (stored in its
// flash) as soon as the link comes up.
struct VideoConfig {
  uint8_t profile;       // LINK_PROFILE_*
  uint8_t quality;       // JPEG quality number: LOWER = better picture and BIGGER frames
  uint8_t hmirror;
  uint8_t vflip;
  uint32_t maxFrame;     // largest frame the bridge can take; bigger frames trigger automatic compression
};
VideoConfig vcfg = { LINK_PROFILE_VGA, 14, 0, 0, 40000 };

uint8_t curQuality = 14;           // quality actually in use (drifts up when frames are too big)
uint16_t smallFrameStreak = 0;     // consecutive frames well below the limit
bool cameraOk = false;
uint8_t captureFailures = 0;
unsigned long lastInitTryMs = 0;
uint8_t initFailures = 0;

uint32_t statFrames = 0, statTooBig = 0, statErrors = 0, statBadPackets = 0;
unsigned long lastStatMs = 0;

uint8_t rxBuf[64];

framesize_t profileToFramesize(uint8_t p) {
  switch (p) {
    case LINK_PROFILE_QVGA: return FRAMESIZE_QVGA;
    case LINK_PROFILE_CIF:  return FRAMESIZE_CIF;
    case LINK_PROFILE_SVGA: return FRAMESIZE_SVGA;
    default:                return FRAMESIZE_VGA;
  }
}

void applySensorSettings() {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return;
  s->set_framesize(s, profileToFramesize(vcfg.profile));
  s->set_quality(s, curQuality);
  s->set_hmirror(s, vcfg.hmirror);
  s->set_vflip(s, vcfg.vflip);
  s->set_brightness(s, 0);
  s->set_contrast(s, 0);
  s->set_saturation(s, 0);
}

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

  config.frame_size = psramFound() ? FRAMESIZE_UXGA : profileToFramesize(vcfg.profile);
  config.jpeg_quality = curQuality;
  if (psramFound()) {
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;     // always hand over the newest picture
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  applySensorSettings();
  Serial.println("Camera initialized");
  return true;
}

// ---- answers to the bridge ----

void sendError(uint16_t seq, uint8_t code) {
  uint8_t p[3] = { (uint8_t)(seq & 0xFF), (uint8_t)(seq >> 8), code };
  linkWriteSimple(Serial2, LINK_T_ERROR, p, 3);
}

void handleFrameRequest(uint16_t seq) {
  if (!cameraOk) {
    sendError(seq, 1);
    return;
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    statErrors++;
    sendError(seq, 2);
    if (++captureFailures > 20) {
      Serial.println("Camera keeps failing -> restarting");
      delay(100);
      ESP.restart();
    }
    return;
  }
  captureFailures = 0;

  if (fb->len > vcfg.maxFrame) {
    // Too big for the bridge: compress harder (higher number) and tell it to ask again.
    esp_camera_fb_return(fb);
    if (curQuality < 40) {
      curQuality = (curQuality + 2 > 40) ? 40 : curQuality + 2;
      sensor_t* s = esp_camera_sensor_get();
      if (s) s->set_quality(s, curQuality);
    }
    statTooBig++;
    smallFrameStreak = 0;
    uint8_t p[3] = { (uint8_t)(seq & 0xFF), (uint8_t)(seq >> 8), curQuality };
    linkWriteSimple(Serial2, LINK_T_TOOBIG, p, 3);
    return;
  }

  uint8_t prefix[2] = { (uint8_t)(seq & 0xFF), (uint8_t)(seq >> 8) };
  linkWritePacket(Serial2, LINK_T_FRAME, prefix, 2, fb->buf, fb->len);
  statFrames++;

  // Frames have been comfortably small for a while: go back towards the quality asked for.
  if (fb->len < (vcfg.maxFrame * 55UL) / 100UL) {
    if (++smallFrameStreak >= 40 && curQuality > vcfg.quality) {
      curQuality--;
      smallFrameStreak = 0;
      sensor_t* s = esp_camera_sensor_get();
      if (s) s->set_quality(s, curQuality);
    }
  } else {
    smallFrameStreak = 0;
  }
  esp_camera_fb_return(fb);
}

void handleConfig(const uint8_t* p, uint32_t len) {
  if (len < 8) return;
  vcfg.profile = (p[0] <= LINK_PROFILE_SVGA) ? p[0] : LINK_PROFILE_VGA;
  vcfg.quality = (p[1] < 8) ? 8 : (p[1] > 40 ? 40 : p[1]);
  vcfg.hmirror = p[2] ? 1 : 0;
  vcfg.vflip = p[3] ? 1 : 0;
  uint32_t mf = linkGetU32(p + 4);
  vcfg.maxFrame = (mf < 5000) ? 5000 : (mf > 120000 ? 120000 : mf);
  curQuality = vcfg.quality;
  smallFrameStreak = 0;

  if (cameraOk) applySensorSettings();
  Serial.printf("Config from bridge: profile=%u quality=%u mirror=%u flip=%u maxFrame=%lu\n",
                vcfg.profile, vcfg.quality, vcfg.hmirror, vcfg.vflip, (unsigned long)vcfg.maxFrame);
  linkWriteSimple(Serial2, LINK_T_CONFIG_ACK, (const uint8_t*)NODE_FW_TAG, strlen(NODE_FW_TAG));
}

void handlePing(const uint8_t* p, uint32_t len) {
  if (len < 4) return;
  uint8_t out[13];
  memcpy(out, p, 4);                                   // echo the nonce
  linkPutU32(out + 4, millis());
  linkPutU32(out + 8, ESP.getFreeHeap());
  out[12] = cameraOk ? 1 : 0;
  linkWriteSimple(Serial2, LINK_T_PONG, out, sizeof(out));
}

void setupWatchdog() {
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = 30000;
  cfg.idle_core_mask = 0;
  cfg.trigger_panic = true;
  if (esp_task_wdt_reconfigure(&cfg) == ESP_ERR_INVALID_STATE) esp_task_wdt_init(&cfg);
  esp_task_wdt_add(NULL);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.printf("=== SurvCam camera node %s ===\n", NODE_FW_TAG);

  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  WiFi.mode(WIFI_OFF);        // this board never uses WiFi
  btStop();

  Serial2.setRxBufferSize(2048);
  Serial2.begin(LINK_BAUD, SERIAL_8N1, LINK_RX_PIN, LINK_TX_PIN);

  cameraOk = initCamera();
  lastInitTryMs = millis();
  setupWatchdog();
}

void loop() {
  esp_task_wdt_reset();

  uint32_t plen = 0;
  int type = linkReadPacket(Serial2, rxBuf, sizeof(rxBuf), &plen, 100);

  if (type == LINK_T_FRAME_REQ && plen >= 2) {
    handleFrameRequest((uint16_t)(rxBuf[0] | (rxBuf[1] << 8)));
  } else if (type == LINK_T_CONFIG) {
    handleConfig(rxBuf, plen);
  } else if (type == LINK_T_PING) {
    handlePing(rxBuf, plen);
  } else if (type == LINK_T_REBOOT) {
    Serial.println("Reboot requested by the bridge");
    delay(100);
    ESP.restart();
  } else if (type < 0) {
    statBadPackets++;
    linkFlush(Serial2);        // get back in step with the bridge
  }

  unsigned long now = millis();

  // Camera did not start: keep trying, and restart after a few failures.
  if (!cameraOk && now - lastInitTryMs > 10000) {
    lastInitTryMs = now;
    cameraOk = initCamera();
    if (!cameraOk && ++initFailures >= 5) {
      Serial.println("Camera never started -> restarting");
      delay(100);
      ESP.restart();
    }
  }

  if (now - lastStatMs > 10000) {
    lastStatMs = now;
    Serial.printf("frames=%lu tooBig=%lu captureErrors=%lu badPackets=%lu quality=%u heap=%u\n",
                  (unsigned long)statFrames, (unsigned long)statTooBig, (unsigned long)statErrors,
                  (unsigned long)statBadPackets, curQuality, ESP.getFreeHeap());
  }
}
