#include "NodeControl.hpp"

#include <Arduino.h>

#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;

namespace bench {

namespace {

constexpr int kAttempts = 3;
constexpr uint32_t kSwitchGuardMs = 1000;   // the gateway rebuilds its radio after a switch

bool sendControl(Transport &link, const uint8_t *frame, size_t len) {
    return link.send(gatewayAddress(kTech), frame, len).acknowledged;
}

void retune(std::unique_ptr<Transport> &link, const LoRaParams &params, uint32_t ackTimeoutMs) {
    link.reset();   // one radio: the old transport goes before the new one comes up
    link = makeLoRa(params, ackTimeoutMs);
    link->setup();
}

}   // namespace

bool negotiate(std::unique_ptr<Transport> &link) {
    uint8_t request[frames::kSwitchBytes];
    frames::build(request, sizeof(request), 'C', 0, kRunId);
    request[frames::kSf] = kPoint.spreadingFactor;
    request[frames::kCr] = kPoint.codingRate;
    request[frames::kPower] = static_cast<uint8_t>(kPoint.txPowerDbm);
    request[frames::kPreamble] = kPoint.preambleLength;
    request[frames::kBwKHz] = kPoint.bandwidthKHz;
    request[frames::kLease4s] = static_cast<uint8_t>((kLeaseSeconds + 3) / 4);

    uint8_t probe[frames::kHeaderBytes];
    frames::build(probe, sizeof(probe), 'N', 0, kRunId);

    for (int attempt = 0; attempt < kAttempts; attempt++) {
        retune(link, kHome, kHomeAckWaitMs);
        bool acked = sendControl(*link, request, sizeof(request));
        delay(kSwitchGuardMs);
        retune(link, kPoint, kAckWaitMs);
        if (acked || sendControl(*link, probe, sizeof(probe))) {
            Serial.printf("HOME,ok,%d\n", attempt + 1);
            return true;
        }
    }
    Serial.println("HOME,noack");
    return false;
}

void collectReport(Transport &link) {
    uint8_t request[frames::kHeaderBytes];
    frames::build(request, sizeof(request), 'R', 0, kRunId);
    bool requested = false;
    for (int attempt = 0; attempt < kAttempts && !requested; attempt++) {
        requested = sendControl(link, request, sizeof(request));
    }
    // The report is the one frame that travels gateway -> node. listen() is what lets the
    // library ack it on LoRa.
    uint32_t start = millis();
    while (requested && millis() - start < kAckWaitMs + 2000) {
        auto message = link.listen(50);
        if (message && frames::isKind(message->payload, 'K', kRunId)) {
            frames::Report report = frames::parseReport(message->payload);
            Serial.printf("REPORT,ok,%u,", report.received);
            if (report.rssiDbm) {
                Serial.printf("%.0f,%.2f", static_cast<double>(*report.rssiDbm),
                              static_cast<double>(*report.snrDb));
            } else {
                Serial.print(',');
            }
            Serial.println();
            return;
        }
    }
    Serial.println(requested ? "REPORT,lost" : "REPORT,noack");
}

}   // namespace bench
