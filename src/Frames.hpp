/**
 * @brief The frames the node and the gateway exchange. Nothing here talks to a radio.
 *
 * Data flows one way: the node sends payloads, the gateway confirms each with a short ack.
 * Every frame starts with the same four bytes; only the ping is padded to the payload size:
 *
 *   [0] kind   [1..2] sequence number, little-endian   [3] run id   [4..] kind-specific, padding
 *
 *   'P' ping      node -> gateway: the payload. Sequence kProbeSeq asks for the counters without
 *                 being counted.
 *   'Q' pong      gateway -> node: the ack, always 8 bytes. [4..5] pings the gateway has received in
 *                 this run, [6] its RSSI of this ping in dBm, [7] its SNR in quarter dB.
 *   'C' switch    node -> gateway on home: the LoRa point to move to, see SwitchField.
 *   'A' 'R' 'K'   switch ack, release (node, on the point), release ack. Header only.
 *
 * The pong carries the gateway's count because the gateway has no host: it is the only way to
 * split a lost round trip into "ping lost" and "pong lost".
 */
#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace bench::frames {

constexpr size_t kHeaderBytes = 8;     // also the smallest frame
constexpr size_t kMaxBytes = 250;      // ESP-NOW's limit, the largest of the three
constexpr size_t kSwitchBytes = 10;
constexpr uint16_t kProbeSeq = 0xFFFF;
constexpr int8_t kNoReading = 0;       // RSSI byte of a radio that reports none

enum SwitchField : size_t { kSf = 4, kCr = 5, kPower = 6, kPreamble = 7, kBwKHz = 8, kLease4s = 9 };

inline void build(uint8_t *buf, size_t len, char kind, uint16_t seq, uint8_t runId) {
    buf[0] = static_cast<uint8_t>(kind);
    buf[1] = static_cast<uint8_t>(seq & 0xFF);
    buf[2] = static_cast<uint8_t>(seq >> 8);
    buf[3] = runId;
    for (size_t i = 4; i < len; i++) {
        buf[i] = static_cast<uint8_t>(i);   // deterministic padding
    }
}

inline uint16_t seqOf(const std::vector<uint8_t> &frame) {
    return static_cast<uint16_t>(frame[1] | (frame[2] << 8));
}

inline bool matches(const std::vector<uint8_t> &frame, char kind, uint16_t seq, uint8_t runId) {
    return frame.size() >= kHeaderBytes && frame[0] == static_cast<uint8_t>(kind) &&
           seqOf(frame) == seq && frame[3] == runId;
}

inline int8_t toByte(std::optional<float> value, float scale) {
    if (!value) {
        return kNoReading;
    }
    return static_cast<int8_t>(std::clamp(std::lround(*value * scale), -127L, 127L));
}

inline void buildPong(uint8_t *buf, size_t len, uint16_t seq, uint8_t runId, uint16_t gatewayRx,
                      std::optional<float> rssiDbm, std::optional<float> snrDb) {
    build(buf, len, 'Q', seq, runId);
    buf[4] = static_cast<uint8_t>(gatewayRx & 0xFF);
    buf[5] = static_cast<uint8_t>(gatewayRx >> 8);
    buf[6] = static_cast<uint8_t>(toByte(rssiDbm, 1.0f));
    buf[7] = static_cast<uint8_t>(toByte(snrDb, 4.0f));
}

struct PongFields {
    uint16_t gatewayRx;
    std::optional<float> rssiDbm;
    std::optional<float> snrDb;
};

inline PongFields pongFields(const std::vector<uint8_t> &frame) {
    PongFields out{static_cast<uint16_t>(frame[4] | (frame[5] << 8)), std::nullopt, std::nullopt};
    if (static_cast<int8_t>(frame[6]) != kNoReading) {
        out.rssiDbm = static_cast<int8_t>(frame[6]);
        out.snrDb = static_cast<int8_t>(frame[7]) / 4.0f;
    }
    return out;
}

}   // namespace bench::frames
