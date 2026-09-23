/** @brief The ping-pong message format and the node's distinct-arrival bitmap. */

#include "BenchExchange.hpp"

#include <algorithm>
#include <cstring>

#include <Arduino.h>

using namespace connectionlib;

namespace bench::detail {

namespace {

// 4096 sequence numbers of distinct-arrival tracking, for receiver-side PDR. 512 bytes of static
// RAM, far more than any single configuration point needs.
constexpr size_t kSeqBitmapWords = 128;

uint32_t g_seqSeen[kSeqBitmapWords] = {};

}   // namespace

NodeCounters &counters() {
    static NodeCounters instance;
    return instance;
}

void resetSeen() {
    std::memset(g_seqSeen, 0, sizeof(g_seqSeen));
}

bool markSeen(uint16_t seq) {
    size_t word = seq / 32;
    if (word >= kSeqBitmapWords) {
        return true;   // beyond the bitmap: count it, just cannot dedupe it
    }
    uint32_t bit = 1u << (seq % 32);
    if (g_seqSeen[word] & bit) {
        return false;
    }
    g_seqSeen[word] |= bit;
    return true;
}

const uint32_t *seenBitmap() {
    return g_seqSeen;
}

size_t seenBitmapWords() {
    return kSeqBitmapWords;
}

void buildMessage(uint8_t *buf, size_t len, char kind, uint16_t seq) {
    buf[0] = static_cast<uint8_t>(kind);
    buf[1] = static_cast<uint8_t>(seq & 0xFF);
    buf[2] = static_cast<uint8_t>((seq >> 8) & 0xFF);
    for (size_t i = kMinPayloadBytes; i < len; i++) {
        buf[i] = static_cast<uint8_t>(i);
    }
}

uint16_t sequenceOf(const std::vector<uint8_t> &payload) {
    return static_cast<uint16_t>(payload[1]) |
           static_cast<uint16_t>(static_cast<uint16_t>(payload[2]) << 8);
}

bool matches(const ReceivedMessage &msg, char kind, uint16_t seq, size_t len) {
    if (msg.payload.size() != len || msg.payload.size() < kMinPayloadBytes) {
        return false;
    }
    return msg.payload[0] == static_cast<uint8_t>(kind) && sequenceOf(msg.payload) == seq;
}

void printOptional(const std::optional<float> &value) {
    if (value.has_value()) {
        Serial.printf("%.1f", static_cast<double>(*value));
    }
}

size_t effectivePayloadBytes(const Transport &transport, const BenchConfig &config) {
    size_t ceiling = std::min(transport.maxPayloadBytes(), kMaxPayloadBytes);
    return std::min(std::max(static_cast<size_t>(config.payloadBytes), kMinPayloadBytes), ceiling);
}

}   // namespace bench::detail
