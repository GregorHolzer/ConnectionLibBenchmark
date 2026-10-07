/**
 * @brief Gateway firmware: flashed once, left in the field with no host. It receives.
 *
 * Confirming delivery is the library's job, not this file's: listen() is all that is called for a
 * data frame, and the radio or the library acks it (Frames.hpp). The application does three
 * things on top:
 *
 *   - counts the data frames of the current run, and averages their RSSI/SNR where the radio
 *     reports them;
 *   - sends that as one report frame when the node asks for it after its run;
 *   - moves LoRa between home and a point. ESP-NOW and Zigbee have a single parameter set and
 *     stay up for good, but LoRa hears only one SF/BW at a time, so it waits on home
 *     (Config.hpp), moves when a node asks, and is back on home when the node says so or the
 *     lease that came with the request runs out.
 *
 * What it prints on serial is only for watching it on the desk.
 */
#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

constexpr uint32_t kAckWaitOwnMs = 1000;   // for the gateway's one send, the report

std::unique_ptr<Transport> g_espNow, g_zigbee, g_lora;

bool g_leased = false;
uint32_t g_leaseStartMs = 0;
uint32_t g_leaseMs = 0;

uint8_t g_runId = 0;
uint16_t g_received = 0;
float g_rssiSum = 0, g_snrSum = 0;
uint16_t g_readings = 0;

void tuneLoRa(const LoRaParams &params, bool leased) {
    g_lora.reset();   // one radio: the old transport goes before the new one comes up
    g_lora = makeLoRa(params, kAckWaitOwnMs);
    bool ok = g_lora->setup();
    g_leased = leased;
    g_leaseStartMs = millis();
    Serial.printf("LORA,%s,sf%u,bw%u,cr%u,%ddBm,%s\n", leased ? "point" : "home",
                  params.spreadingFactor, params.bandwidthKHz, params.codingRate, params.txPowerDbm,
                  ok ? "ok" : "SETUP FAILED");
}

/** @brief A switch request is honoured only inside the EU envelope the node was built against. */
bool acceptable(const LoRaParams &p) {
    return p.spreadingFactor >= 7 && p.spreadingFactor <= 12 && p.codingRate >= 5 &&
           p.codingRate <= 8 && p.txPowerDbm <= 7 && (p.bandwidthKHz == 125 || p.bandwidthKHz == 250);
}

void countData(const ReceivedMessage &message) {
    if (message.payload[3] != g_runId) {   // a new run: start counting again
        g_runId = message.payload[3];
        g_received = g_readings = 0;
        g_rssiSum = g_snrSum = 0;
    }
    g_received++;
    if (message.rssiDbm && message.snrDb) {
        g_rssiSum += *message.rssiDbm;
        g_snrSum += *message.snrDb;
        g_readings++;
    }
}

void serve(Transport &link) {
    auto message = link.listen(0);   // on LoRa the library acks the frame in here
    if (!message || message->payload.size() < frames::kHeaderBytes) {
        return;
    }
    const auto &in = message->payload;
    const bool isLoRa = &link == g_lora.get();

    if (in[0] == 'D') {
        countData(*message);
    } else if (in[0] == 'R') {
        frames::Report report;
        report.received = in[3] == g_runId ? g_received : 0;
        if (report.received && g_readings) {
            report.rssiDbm = g_rssiSum / g_readings;
            report.snrDb = g_snrSum / g_readings;
        }
        uint8_t out[frames::kHeaderBytes];
        frames::buildReport(out, in[3], report);
        link.send(message->source, out, sizeof(out));
        Serial.printf("RUN,%u,%s,received %u\n", in[3], link.technologyName(), report.received);
        if (isLoRa && g_leased) {
            tuneLoRa(kHome, false);
        }
    } else if (in[0] == 'C' && isLoRa && in.size() >= frames::kSwitchBytes) {
        LoRaParams point{in[frames::kBwKHz], in[frames::kSf], in[frames::kCr],
                         static_cast<int8_t>(in[frames::kPower]), in[frames::kPreamble]};
        if (acceptable(point)) {
            g_leaseMs = in[frames::kLease4s] * 4000UL;
            tuneLoRa(point, true);
        }
    }
}

}   // namespace

void setup() {
    Serial.begin(115200);
    delay(500);
    board::powerUp(false);   // ESP-NOW needs WiFi, and here it is up for good
    g_espNow = makeEspNow();
    g_zigbee = makeZigbee();
    Serial.printf("GATEWAY,espnow,%s\n", g_espNow->setup() ? "ok" : "SETUP FAILED");
    Serial.printf("GATEWAY,zigbee,%s\n", g_zigbee->setup() ? "ok" : "SETUP FAILED");
    tuneLoRa(kHome, false);
}

void loop() {
    serve(*g_espNow);
    serve(*g_zigbee);
    serve(*g_lora);
    if (g_leased && millis() - g_leaseStartMs > g_leaseMs) {
        Serial.println("LORA,lease expired");
        tuneLoRa(kHome, false);
    }
}
