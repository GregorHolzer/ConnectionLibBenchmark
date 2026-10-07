/**
 * @brief The application frames of the benchmark. Nothing here talks to a radio.
 *
 * Delivery of every frame is confirmed by the library, not by these frames: ESP-NOW's MAC ack,
 * Zigbee's APS ack, and ConnectionLib's software ack on LoRa (CONNECTIONLIB_LORA_ACK). send()
 * returns `acknowledged`, and that is the benchmark's delivery signal. The gateway application
 * never answers a data frame.
 *
 *   [0] kind   [1..2] sequence number, little-endian   [3] run id   [4..] kind-specific, padding
 *
 *   'D' data      node -> gateway, padded to the configured payload size. The measured traffic.
 *   'C' switch    node -> gateway on LoRa home: the LoRa point to move to, see SwitchField.
 *   'N' probe     node -> gateway, header only: "are you on this LoRa point?" Answered by the
 *                 library's ack alone.
 *   'R' request   node -> gateway after the run: send your report, and (LoRa) go back to home.
 *   'K' report    gateway -> node, the only frame the gateway application ever sends:
 *                 [4..5] data frames received in this run, [6] their mean RSSI in dBm,
 *                 [7] their mean SNR in quarter dB.
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

inline bool isKind(const std::vector<uint8_t> &frame, char kind, uint8_t runId) {
    return frame.size() >= kHeaderBytes && frame[0] == static_cast<uint8_t>(kind) &&
           frame[3] == runId;
}

struct Report {
    uint16_t received = 0;
    std::optional<float> rssiDbm;
    std::optional<float> snrDb;
};

inline void buildReport(uint8_t *buf, uint8_t runId, const Report &report) {
    build(buf, kHeaderBytes, 'K', 0, runId);
    buf[4] = static_cast<uint8_t>(report.received & 0xFF);
    buf[5] = static_cast<uint8_t>(report.received >> 8);
    auto toByte = [](float value) {
        return static_cast<uint8_t>(static_cast<int8_t>(std::clamp(std::lround(value), -127L, 127L)));
    };
    buf[6] = report.rssiDbm ? toByte(*report.rssiDbm) : static_cast<uint8_t>(kNoReading);
    buf[7] = report.snrDb ? toByte(*report.snrDb * 4.0f) : 0;
}

inline Report parseReport(const std::vector<uint8_t> &frame) {
    Report out;
    out.received = static_cast<uint16_t>(frame[4] | (frame[5] << 8));
    if (static_cast<int8_t>(frame[6]) != kNoReading) {
        out.rssiDbm = static_cast<int8_t>(frame[6]);
        out.snrDb = static_cast<int8_t>(frame[7]) / 4.0f;
    }
    return out;
}

}   // namespace bench::frames
