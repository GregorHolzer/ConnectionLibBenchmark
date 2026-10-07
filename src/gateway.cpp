/**
 * @brief Gateway firmware: flashed once, left in the field with no host.
 *
 * It is in one of two states, with exactly one radio up in either:
 *
 *   WAIT  LoRa on home (Config.hpp), nothing else. A config frame from the node says which radio
 *         the next run uses, with which LoRa parameters, and for how long at most.
 *   RUN   only that radio. The loop is listen() and a counter. An end frame from the node, or the
 *         lease running out, leads back to WAIT.
 *
 * Confirming delivery is never this file's job: the radio or the library acks every frame from
 * inside listen() (Frames.hpp). The one frame the application sends is the report that answers
 * the end frame. What it prints on serial is only for watching it on the desk.
 */
#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

constexpr uint32_t kOwnAckWaitMs = 1000;   // for the gateway's one send, the report

std::unique_ptr<Transport> g_link;   // the one radio that is up
bool g_running = false;
uint32_t g_runStartMs = 0;
uint32_t g_leaseMs = 0;

frames::Report g_report;   // what this run has received so far
float g_rssiSum = 0, g_snrSum = 0;
uint16_t g_readings = 0;

void enterWait() {
    g_link.reset();   // before the next radio comes up: there is never more than one
    board::wifiOff();
    board::loRaRail(true);
    g_link = makeLoRa(kHome, kOwnAckWaitMs);
    g_running = false;
    Serial.printf("WAIT,%s\n", g_link->setup() ? "ok" : "SETUP FAILED");
}

void enterRun(Tech tech, const LoRaParams &point, uint32_t leaseMs) {
    g_link.reset();
    if (tech != Tech::LoRa) {
        board::loRaRail(false);
    }
    g_link = makeTransport(tech, point, kOwnAckWaitMs);
    bool ok = g_link->setup();
    g_running = true;
    g_runStartMs = millis();
    g_leaseMs = leaseMs;
    g_report = {};
    g_rssiSum = g_snrSum = 0;
    g_readings = 0;
    Serial.printf("RUN,%s,sf%u,bw%u,cr%u,%ddBm,%s\n", g_link->technologyName(),
                  point.spreadingFactor, point.bandwidthKHz, point.codingRate, point.txPowerDbm,
                  ok ? "ok" : "SETUP FAILED");
}

/** @brief A config is honoured only inside the EU envelope the node was built against. */
bool acceptable(uint8_t tech, const LoRaParams &p) {
    return tech >= static_cast<uint8_t>(Tech::EspNow) && tech <= static_cast<uint8_t>(Tech::Zigbee) &&
           p.spreadingFactor >= 7 && p.spreadingFactor <= 12 && p.codingRate >= 5 &&
           p.codingRate <= 8 && p.txPowerDbm <= 7 && (p.bandwidthKHz == 125 || p.bandwidthKHz == 250);
}

void onConfig(const std::vector<uint8_t> &in) {
    if (in[0] != 'C' || in.size() < frames::kConfigBytes) {
        return;
    }
    LoRaParams point{in[frames::kBwKHz], in[frames::kSf], in[frames::kCr],
                     static_cast<int8_t>(in[frames::kPower]), in[frames::kPreamble]};
    if (acceptable(in[frames::kTechnology], point)) {
        enterRun(static_cast<Tech>(in[frames::kTechnology]), point, in[frames::kLease4s] * 4000UL);
    }
}

void onRunFrame(const ReceivedMessage &message) {
    const auto &in = message.payload;
    if (in[0] == 'D') {
        g_report.received++;
        if (message.rssiDbm && message.snrDb) {
            g_rssiSum += *message.rssiDbm;
            g_snrSum += *message.snrDb;
            g_readings++;
        }
    } else if (in[0] == 'E') {
        if (g_readings > 0) {
            g_report.rssiDbm = g_rssiSum / g_readings;
            g_report.snrDb = g_snrSum / g_readings;
        }
        uint8_t out[frames::kHeaderBytes];
        frames::buildReport(out, in[3], g_report);
        g_link->send(message.source, out, sizeof(out));
        Serial.printf("END,received %u\n", g_report.received);
        enterWait();
    }
}

}   // namespace

void setup() {
    Serial.begin(115200);
    delay(500);
    board::begin();
    enterWait();
}

void loop() {
    // One radio, one non-blocking check, as often as the CPU allows. On LoRa the library sends
    // its ack from inside this call.
    auto message = g_link->listen(0);
    if (message && message->payload.size() >= frames::kHeaderBytes) {
        if (g_running) {
            onRunFrame(*message);
        } else {
            onConfig(message->payload);
        }
    }
    if (g_running && millis() - g_runStartMs > g_leaseMs) {
        Serial.println("LEASE,expired");
        enterWait();
    }
}
