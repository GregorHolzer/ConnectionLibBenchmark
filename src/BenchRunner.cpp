#include "BenchRunner.hpp"

#include <algorithm>
#include <cstring>
#include <optional>

#include <Arduino.h>

using namespace connectionlib;

namespace bench {

namespace {

// Poll granularity while waiting for a reply. The original harness used delay(5), which put a hard
// 5ms floor under every latency figure - every ESP-NOW exchange read exactly 5.00ms, i.e. the
// harness was being reported rather than the radio. 200us resolves ESP-NOW comfortably while still
// yielding to the ESP32's WiFi task.
constexpr uint32_t kPollIntervalUs = 200;

// 4096 sequence numbers of distinct-arrival tracking, for receiver-side PDR. 512 bytes of static
// RAM, far more than any single configuration point needs.
constexpr size_t kSeqBitmapWords = 128;

uint32_t g_rxTotal = 0;
uint32_t g_rxDistinct = 0;
uint32_t g_repliesOk = 0;
uint32_t g_replyFailures = 0;
uint32_t g_seqSeen[kSeqBitmapWords] = {};

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

/** @brief CSV field for an optional metric: the value, or empty where the radio can't report it. */
void printOptional(const std::optional<float> &value) {
    if (value.has_value()) {
        Serial.printf("%.1f", static_cast<double>(*value));
    }
}

size_t effectivePayloadBytes(const Transport &transport, const BenchConfig &config) {
    size_t ceiling = std::min(transport.maxPayloadBytes(), kMaxPayloadBytes);
    return std::min(std::max(static_cast<size_t>(config.payloadBytes), kMinPayloadBytes), ceiling);
}

}   // namespace

void resetNodeCounters() {
    g_rxTotal = 0;
    g_rxDistinct = 0;
    g_repliesOk = 0;
    g_replyFailures = 0;
    std::memset(g_seqSeen, 0, sizeof(g_seqSeen));
}

void runGatewayPoint(Transport &transport, const Address &peer, const BenchConfig &config) {
    size_t payloadLen = effectivePayloadBytes(transport, config);
    uint8_t buf[kMaxPayloadBytes];

    uint32_t seq = 0;
    uint32_t attemptsForSeq = 0;
    uint32_t attempts = 0;
    uint32_t retries = 0;
    uint32_t dutyBlocked = 0;
    uint64_t sendUsTotal = 0;
    uint32_t lastAttemptMs = 0;
    uint32_t startMs = millis();

    while (seq < config.exchanges) {
        // Pacing floor between attempts, fresh or retry. Not the exchange rate: LoRa's
        // time-on-air and Zigbee's status wait both exceed it comfortably.
        if (lastAttemptMs != 0 && millis() - lastAttemptMs < config.spacingMs) {
            delay(1);
            continue;
        }

        buildMessage(buf, payloadLen, 'P', static_cast<uint16_t>(seq));

        // t0 before send(), deliberately. See docs/metrics.md.
        uint32_t t0 = micros();
        SendResult result = transport.send(peer, buf, payloadLen);
        uint32_t sendUs = micros() - t0;

        if (result.status == SendStatus::DutyCycleExceeded) {
            // Not a real attempt. The host paces LoRa against the rolling budget so this should
            // stay rare, but if the guard does bite, waiting is the correct response.
            dutyBlocked++;
            delay(50);
            continue;
        }

        if (attemptsForSeq > 0) {
            retries++;
        }
        attemptsForSeq++;
        attempts++;
        lastAttemptMs = millis();
        sendUsTotal += sendUs;

        uint32_t rttUs = 0;
        bool ok = false;
        if (result.status == SendStatus::Ok) {
            uint32_t waitStart = millis();
            while (millis() - waitStart < config.replyWaitMs) {
                auto received = transport.poll();
                if (received.has_value() &&
                    matches(*received, 'Q', static_cast<uint16_t>(seq), payloadLen)) {
                    rttUs = micros() - t0;
                    ok = true;
                    break;
                }
                delayMicroseconds(kPollIntervalUs);
            }
        }

        // One record per attempt, successes and failures alike, so retries and failure modes stay
        // visible to the analysis instead of collapsing into aggregate counts.
        Serial.printf("PPD,%lu,%lu,%u,%u,%lu,%lu,%d,%d,", static_cast<unsigned long>(seq),
                       static_cast<unsigned long>(attemptsForSeq), static_cast<unsigned>(payloadLen),
                       static_cast<unsigned>(result.totalBytes), static_cast<unsigned long>(sendUs),
                       static_cast<unsigned long>(rttUs), static_cast<int>(result.status),
                       result.acknowledged ? 1 : 0);
        printOptional(result.rssiDbm);
        Serial.print(',');
        printOptional(result.snrDb);
        Serial.printf(",%.4f,%d\n", static_cast<double>(transport.currentDutyCyclePercent()),
                       ok ? 1 : 0);

        if (ok) {
            seq++;
            attemptsForSeq = 0;
        } else if (attemptsForSeq >= 5) {
            // Give up on this sequence number rather than retrying forever. A configuration that
            // cannot complete is itself a result; stalling the campaign is not.
            seq++;
            attemptsForSeq = 0;
        }
    }

    Serial.printf("PPS,%lu,%lu,%lu,%lu,%.4f,%llu,%lu\n", static_cast<unsigned long>(seq),
                   static_cast<unsigned long>(attempts), static_cast<unsigned long>(retries),
                   static_cast<unsigned long>(millis() - startMs),
                   static_cast<double>(transport.currentDutyCyclePercent()),
                   static_cast<unsigned long long>(sendUsTotal),
                   static_cast<unsigned long>(dutyBlocked));
}

void nodeTick(Transport &transport) {
    auto received = transport.poll();
    if (!received.has_value()) {
        return;
    }

    const auto &payload = received->payload;
    if (payload.size() < kMinPayloadBytes || payload[0] != static_cast<uint8_t>('P')) {
        return;   // not one of our pings
    }
    uint16_t seq = sequenceOf(payload);

    g_rxTotal++;
    if (markSeen(seq)) {
        g_rxDistinct++;
    }

    Serial.printf("PPN,%u,%u,%u,", static_cast<unsigned>(seq), static_cast<unsigned>(payload.size()),
                   static_cast<unsigned>(received->totalBytes));
    printOptional(received->rssiDbm);
    Serial.print(',');
    printOptional(received->snrDb);
    Serial.println();

    // Reply is the same size as the ping, so both legs carry an identical payload.
    uint8_t reply[kMaxPayloadBytes];
    size_t replyLen = std::min(payload.size(), kMaxPayloadBytes);
    buildMessage(reply, replyLen, 'Q', seq);

    SendResult result = transport.send(received->source, reply, replyLen);
    if (result.status == SendStatus::Ok) {
        g_repliesOk++;
    } else {
        g_replyFailures++;
    }

    Serial.printf("PPM,%lu,%lu,%lu,%lu\n", static_cast<unsigned long>(g_rxDistinct),
                   static_cast<unsigned long>(g_rxTotal), static_cast<unsigned long>(g_repliesOk),
                   static_cast<unsigned long>(g_replyFailures));
}

void runNullMeasurement(const BenchConfig &config) {
    // Everything the gateway loop does per exchange except touching a radio: the two micros()
    // reads, the payload construction, and one pass of the poll-wait body. Whatever this costs is
    // the floor under every latency number the suite reports.
    uint8_t buf[kMaxPayloadBytes];
    size_t payloadLen = std::min(std::max(static_cast<size_t>(config.payloadBytes), kMinPayloadBytes),
                                  kMaxPayloadBytes);

    for (uint32_t i = 0; i < config.exchanges; i++) {
        uint32_t t0 = micros();
        buildMessage(buf, payloadLen, 'P', static_cast<uint16_t>(i));
        uint32_t overheadUs = micros() - t0;
        Serial.printf("PPD,%lu,1,%u,%u,%lu,%lu,0,0,,,0.0000,1\n", static_cast<unsigned long>(i),
                       static_cast<unsigned>(payloadLen), static_cast<unsigned>(payloadLen),
                       static_cast<unsigned long>(overheadUs), static_cast<unsigned long>(overheadUs));
    }
    Serial.printf("PPS,%lu,%lu,0,0,0.0000,0,0\n", static_cast<unsigned long>(config.exchanges),
                   static_cast<unsigned long>(config.exchanges));
}

}   // namespace bench
