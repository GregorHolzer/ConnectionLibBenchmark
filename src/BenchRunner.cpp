/**
 * @brief Gateway role: the measured exchange loop, one configuration point per call.
 *
 * Strict request/response - never more than one ping in flight, and ping N+1 is not sent until
 * ping N is answered. Ported from ConnectionLib-examples' pingpong, keeping its corrected timing
 * and generalised from build-time macros to a runtime BenchConfig.
 *
 * The message format and the counters live in BenchExchange; the node's half is in BenchNode.cpp.
 */

#include "BenchRunner.hpp"

#include <algorithm>

#include <Arduino.h>

#include "BenchExchange.hpp"

using namespace connectionlib;
using namespace bench::detail;

namespace bench {

void resetNodeCounters() {
    detail::counters() = detail::NodeCounters{};
    detail::resetSeen();
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
        // Any input means the host has given up on this point (Device.resync()); stop, and let the
        // command loop answer it. Checked here, between attempts and outside every timed region,
        // so it costs the measurement nothing. Without it a point that overran its timeout kept
        // retrying for up to 5 x replywait per exchange - 14 minutes at SF12 - while the host's
        // next commands sat unread in the serial buffer.
        if (Serial.available() > 0) {
            break;
        }

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
        // Signal quality of the *reply*, not of the send. RSSI and SNR are receive-side
        // quantities - no backend populates SendResult's copies, and printing those is why this
        // column was empty in every campaign before now. The reply is the only packet this board
        // receives in an exchange, so its metrics are the ones the gateway can honestly report.
        std::optional<float> replyRssi;
        std::optional<float> replySnr;
        if (result.status == SendStatus::Ok) {
            uint32_t waitStart = millis();
            while (millis() - waitStart < config.replyWaitMs) {
                auto received = transport.poll();
                if (received.has_value() &&
                    matches(*received, 'Q', static_cast<uint16_t>(seq), payloadLen)) {
                    rttUs = micros() - t0;
                    ok = true;
                    replyRssi = received->rssiDbm;
                    replySnr = received->snrDb;
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
        printOptional(replyRssi);
        Serial.print(',');
        printOptional(replySnr);
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
}   // namespace bench
