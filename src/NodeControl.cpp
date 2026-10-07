#include "NodeControl.hpp"

#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;

namespace bench {

namespace {

constexpr int kAttempts = 3;
constexpr uint32_t kSwitchGuardMs = 1000;   // the gateway brings its radio up after a config

bool acked(Transport &link, Tech tech, const uint8_t *frame, size_t len) {
    return link.send(gatewayAddress(tech), frame, len).acknowledged;
}

}   // namespace

bool announce(std::unique_ptr<Transport> &link) {
    uint8_t config[frames::kConfigBytes];
    frames::build(config, sizeof(config), 'C', 0, kRunId);
    config[frames::kSf] = kPoint.spreadingFactor;
    config[frames::kCr] = kPoint.codingRate;
    config[frames::kPower] = static_cast<uint8_t>(kPoint.txPowerDbm);
    config[frames::kPreamble] = kPoint.preambleLength;
    config[frames::kBwKHz] = kPoint.bandwidthKHz;
    config[frames::kLease4s] = static_cast<uint8_t>((kLeaseSeconds + 3) / 4);
    config[frames::kTechnology] = static_cast<uint8_t>(kTech);

    uint8_t probe[frames::kHeaderBytes];
    frames::build(probe, sizeof(probe), 'N', 0, kRunId);

    for (int attempt = 0; attempt < kAttempts; attempt++) {
        link.reset();   // one radio at a time: the old transport goes before the next comes up
        board::loRaRail(true);
        link = makeLoRa(kHome, kHomeAckWaitMs);
        bool announced = link->setup() && acked(*link, Tech::LoRa, config, sizeof(config));
        link.reset();
        if (kTech != Tech::LoRa) {
            board::loRaRail(false);   // or the SX1278 would keep receiving under the whole run
        }
        delay(kSwitchGuardMs);
        link = makeTransport(kTech, kPoint, kAckWaitMs);
        if (!link->setup()) {
            return false;
        }
        if (announced || acked(*link, kTech, probe, sizeof(probe))) {
            Serial.printf("HOME,ok,%d\n", attempt + 1);
            return true;
        }
    }
    Serial.println("HOME,noack");
    return false;
}

void endRun(Transport &link) {
    uint8_t request[frames::kHeaderBytes];
    frames::build(request, sizeof(request), 'E', 0, kRunId);
    bool requested = false;
    for (int attempt = 0; attempt < kAttempts && !requested; attempt++) {
        requested = acked(link, kTech, request, sizeof(request));
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
