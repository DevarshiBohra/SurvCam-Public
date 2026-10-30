#pragma once
// SurvLink - a tiny framed, CRC-protected protocol between the camera board and the bridge
// board over a UART. IDENTICAL COPIES of this file must sit in both sketch folders.
//
// Packet on the wire:
//   0xA5 0x5A | type (1) | payload length (4, little endian) | payload | CRC32 (4, little endian)
// The CRC32 covers type + length + payload. A packet with a bad CRC is simply dropped, so a
// glitch on the wire costs one frame instead of showing broken video.
//
// The bridge always ASKS for a frame ('F') and the camera board answers with one ('J').
// Nothing is ever queued, so slow WiFi can never make the picture fall behind.

#include <Arduino.h>

#define LINK_MAGIC0 0xA5
#define LINK_MAGIC1 0x5A

// bridge -> camera board
#define LINK_T_FRAME_REQ   'F'   // payload: seq (u16)
#define LINK_T_CONFIG      'C'   // payload: profile, quality, hmirror, vflip (u8 each) + max frame bytes (u32)
#define LINK_T_PING        'P'   // payload: nonce (u32)
#define LINK_T_REBOOT      'R'   // payload: none

// camera board -> bridge
#define LINK_T_FRAME       'J'   // payload: seq (u16) + JPEG data
#define LINK_T_TOOBIG      'X'   // payload: seq (u16) + new quality (u8): frame was too big, try again
#define LINK_T_ERROR       'E'   // payload: seq (u16) + code (u8): 1 = camera not ready, 2 = capture failed
#define LINK_T_CONFIG_ACK  'c'   // payload: camera board's own firmware tag (text)
#define LINK_T_PONG        'p'   // payload: nonce (u32) + uptime ms (u32) + free heap (u32) + camera ok (u8)

#define LINK_ABS_MAX_PAYLOAD 200000UL   // anything larger is treated as line noise

// Video profiles (the bridge sends a number, the camera board maps it to its own constants).
#define LINK_PROFILE_QVGA  0   // 320 x 240
#define LINK_PROFILE_CIF   1   // 400 x 296
#define LINK_PROFILE_VGA   2   // 640 x 480
#define LINK_PROFILE_SVGA  3   // 800 x 600

static uint32_t linkCrcTable[256];
static bool linkCrcReady = false;

inline void linkCrcInit() {
  for (uint32_t i = 0; i < 256; i++) {
    uint32_t c = i;
    for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320UL ^ (c >> 1)) : (c >> 1);
    linkCrcTable[i] = c;
  }
  linkCrcReady = true;
}

// Standard CRC-32 (same as zlib). Pass the previous result to continue over more data.
inline uint32_t linkCrc(uint32_t crc, const uint8_t* data, size_t len) {
  if (!linkCrcReady) linkCrcInit();
  crc = ~crc;
  for (size_t i = 0; i < len; i++) crc = linkCrcTable[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

inline void linkPutU32(uint8_t* p, uint32_t v) {
  p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}

inline uint32_t linkGetU32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// Sends one packet. The payload can be given in two pieces (a short prefix and a long body)
// so a camera frame never has to be copied.
inline void linkWritePacket(Stream& s, uint8_t type, const uint8_t* a, uint32_t alen,
                            const uint8_t* b, uint32_t blen) {
  uint8_t hdr[7];
  hdr[0] = LINK_MAGIC0;
  hdr[1] = LINK_MAGIC1;
  hdr[2] = type;
  linkPutU32(hdr + 3, alen + blen);

  uint32_t crc = linkCrc(0, hdr + 2, 5);
  if (alen) crc = linkCrc(crc, a, alen);
  if (blen) crc = linkCrc(crc, b, blen);

  s.write(hdr, 7);
  if (alen) s.write(a, alen);
  if (blen) s.write(b, blen);

  uint8_t tail[4];
  linkPutU32(tail, crc);
  s.write(tail, 4);
}

inline void linkWriteSimple(Stream& s, uint8_t type, const uint8_t* payload, uint32_t len) {
  linkWritePacket(s, type, payload, len, NULL, 0);
}

// Reads exactly n bytes before the deadline. Returns false on timeout.
inline bool linkReadExact(Stream& s, uint8_t* dst, uint32_t n, uint32_t startMs, uint32_t timeoutMs) {
  uint32_t got = 0;
  while (got < n) {
    int avail = s.available();
    if (avail <= 0) {
      if (millis() - startMs > timeoutMs) return false;
      delay(1);
      continue;
    }
    uint32_t k = (uint32_t)avail < (n - got) ? (uint32_t)avail : (n - got);
    got += s.readBytes(dst + got, k);
  }
  return true;
}

// Throws away everything waiting on the line (used to get back in step after an error).
inline void linkFlush(Stream& s) {
  while (s.available() > 0) s.read();
}

// Reads one packet into buf (at most cap bytes of payload).
// Returns: the packet type (> 0) on success, with *plen = payload length
//          0   timeout (nothing arrived, or the packet was cut short)
//         -1   damaged packet (bad CRC or impossible length)
//         -2   payload bigger than cap: it was read and dropped, the line is still in step
inline int linkReadPacket(Stream& s, uint8_t* buf, uint32_t cap, uint32_t* plen, uint32_t timeoutMs) {
  uint32_t start = millis();

  // 1. find the two magic bytes (anything else is noise and skipped)
  int state = 0;
  while (true) {
    if (millis() - start > timeoutMs) return 0;
    if (s.available() <= 0) {
      delay(1);
      continue;
    }
    int c = s.read();
    if (state == 0) {
      if (c == LINK_MAGIC0) state = 1;
    } else {
      if (c == LINK_MAGIC1) break;
      state = (c == LINK_MAGIC0) ? 1 : 0;
    }
  }

  // 2. type + length
  uint8_t head[5];
  if (!linkReadExact(s, head, 5, start, timeoutMs)) return 0;
  uint8_t type = head[0];
  uint32_t len = linkGetU32(head + 1);
  if (len > LINK_ABS_MAX_PAYLOAD) return -1;
  uint32_t crc = linkCrc(0, head, 5);

  // 3. payload (dropped if it would not fit)
  bool tooBig = len > cap;
  uint8_t scratch[128];
  uint32_t got = 0;
  while (got < len) {
    uint32_t chunk = len - got;
    uint8_t* dst;
    if (tooBig) {
      if (chunk > sizeof(scratch)) chunk = sizeof(scratch);
      dst = scratch;
    } else {
      dst = buf + got;
    }
    if (!linkReadExact(s, dst, chunk, start, timeoutMs)) return 0;
    crc = linkCrc(crc, dst, chunk);
    got += chunk;
  }

  // 4. CRC
  uint8_t tail[4];
  if (!linkReadExact(s, tail, 4, start, timeoutMs)) return 0;
  if (linkGetU32(tail) != crc) return -1;

  *plen = len;
  return tooBig ? -2 : (int)type;
}