#include "NodeLoRa.hpp"

#include <Arduino.h>

#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;

namespace bench {

namespace {

constexpr uint32_t kPollIntervalUs = 200;
constexpr uint32_t kHomeAckWaitMs = 5000;   // the ack is ~1.5 s of SF12 airtime
constexpr uint32_t kSwitchGuardMs = 1000;   // the gateway rebuilds its radio after acking

/** @brief Send one header-only control frame and wait for the frame that answers it. */
std::optional<ReceivedMessage> control(Transport &link, const uint8_t *frame, size_t len,
                                       char answer, uint16_t seq, uint32_t waitMs) {
    if (link.send(gatewayAddress(Tech::LoRa), frame, len).status != SendStatus::Ok) {
        return std::nullopt;
    }
    return await(link, answer, seq, waitMs);
}

}   // namespace

std::optional<ReceivedMessage> await(Transport &link, char kind, uint16_t seq, uint32_t waitMs) {
    uint32_t start = millis();
    while (millis() - start < waitMs) {
        auto message = link.listen(0);
        if (message && frames::matches(message->payload, kind, seq, kRunId)) {
            return message;
        }
        delayMicroseconds(kPollIntervalUs);
    }
    return std::nullopt;
}

bool negotiate(std::unique_ptr<Transport> &link) {
    uint8_t frame[frames::kSwitchBytes];
    frames::build(frame, sizeof(frame), 'C', 0, kRunId);
    frame[frames::kSf] = kPoint.spreadingFactor;
    frame[frames::kCr] = kPoint.codingRate;
    frame[frames::kPower] = static_cast<uint8_t>(kPoint.txPowerDbm);
    frame[frames::kPreamble] = kPoint.preambleLength;
    frame[frames::kBwKHz] = kPoint.bandwidthKHz;
    frame[frames::kLease4s] = static_cast<uint8_t>((kLeaseSeconds + 3) / 4);

    uint8_t probe[frames::kHeaderBytes];
    frames::build(probe, sizeof(probe), 'P', frames::kProbeSeq, kRunId);

    for (int attempt = 0; attempt < kControlAttempts; attempt++) {
        link.reset();   // one radio: the old transport goes before the new one comes up
        link = makeLoRa(kHome);
        auto ack = link->setup() ? control(*link, frame, sizeof(frame), 'A', 0, kHomeAckWaitMs)
                                 : std::nullopt;
        if (ack) {
            Serial.printf("HOME,ok,%.1f,%.1f,%d\n", static_cast<double>(ack->rssiDbm.value_or(0)),
                          static_cast<double>(ack->snrDb.value_or(0)), attempt + 1);
        }
        delay(kSwitchGuardMs);
        link.reset();
        link = makeLoRa(kPoint);
        if (!link->setup()) {
            return false;
        }
        if (ack || control(*link, probe, sizeof(probe), 'Q', frames::kProbeSeq, kReplyWaitMs)) {
            return true;
        }
    }
    Serial.println("HOME,noack");
    return false;
}

void release(Transport &link) {
    uint8_t frame[frames::kHeaderBytes];
    frames::build(frame, sizeof(frame), 'R', 0, kRunId);
    for (int attempt = 0; attempt < kControlAttempts; attempt++) {
        if (control(link, frame, sizeof(frame), 'K', 0, kReplyWaitMs)) {
            Serial.println("RELEASE,ok");
            return;
        }
    }
    Serial.println("RELEASE,noack");
}

}   // namespace bench
