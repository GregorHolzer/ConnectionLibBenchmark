/**
 * @brief Gateway firmware: flashed once, left in the field with no host, answers whatever arrives.
 *
 * ESP-NOW and Zigbee each have a single parameter set, so both stay up for good. LoRa hears only
 * one SF/BW at a time, so it waits on home (Config.hpp) and moves to a point when a node asks,
 * for at most the lease that came with the request. Whatever happens to the node, the gateway is
 * back on home when the lease runs out.
 *
 * It keeps no log. Its count of received pings travels back in every pong (Frames.hpp), and what
 * it prints on serial is only for watching it on the desk.
 */
#include <Arduino.h>

#include <ConnectionLib/DutyCycleGuard.hpp>

#include "Board.hpp"
#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

std::unique_ptr<Transport> g_espNow, g_zigbee, g_lora;

// The LoRa transport's own duty-cycle guard starts empty every time the radio is rebuilt, i.e. on
// every switch. This one spans the gateway's whole uptime; a reply it cannot afford is not sent.
DutyCycleGuard g_loraBudget({10.0f, 3'600'000});
uint32_t g_lastLoRaAirMs = 0;

bool g_leased = false;
uint32_t g_leaseStartMs = 0;
uint32_t g_leaseMs = 0;

uint8_t g_runId = 0;
uint16_t g_received = 0;   // pings in the current run, probes excluded

void tuneLoRa(const LoRaParams &params, bool leased) {
    g_lora.reset();   // one radio: the old transport goes before the new one comes up
    g_lora = makeLoRa(params);
    bool ok = g_lora->setup();
    g_leased = leased;
    g_leaseStartMs = millis();
    Serial.printf("LORA,%s,sf%u,bw%u,cr%u,%ddBm,%s\n", leased ? "point" : "home",
                  params.spreadingFactor, params.bandwidthKHz, params.codingRate, params.txPowerDbm,
                  ok ? "ok" : "SETUP FAILED");
}

void reply(Transport &link, const Address &to, const uint8_t *frame, size_t len) {
    bool isLoRa = &link == g_lora.get();
    if (isLoRa && !g_loraBudget.canTransmit(millis(), g_lastLoRaAirMs)) {
        Serial.println("DROP,duty cycle");
        return;
    }
    uint32_t start = millis();
    link.send(to, frame, len);
    if (isLoRa) {
        g_lastLoRaAirMs = millis() - start;
        g_loraBudget.recordTransmission(start, g_lastLoRaAirMs);
    }
}

/** @brief A switch request is honoured only inside the EU envelope the node was built against. */
bool acceptable(const LoRaParams &p) {
    return p.spreadingFactor >= 7 && p.spreadingFactor <= 12 && p.codingRate >= 5 &&
           p.codingRate <= 8 && p.txPowerDbm <= 7 && (p.bandwidthKHz == 125 || p.bandwidthKHz == 250);
}

void serve(Transport &link) {
    auto message = link.listen(0);
    if (!message || message->payload.size() < frames::kHeaderBytes) {
        return;
    }
    const auto &in = message->payload;
    const size_t len = std::min(in.size(), frames::kMaxBytes);
    const uint16_t seq = frames::seqOf(in);
    const uint8_t runId = in[3];
    const bool isLoRa = &link == g_lora.get();
    uint8_t out[frames::kMaxBytes];

    switch (in[0]) {
        case 'P':
            if (runId != g_runId) {
                g_runId = runId;
                g_received = 0;
                Serial.printf("RUN,%u,%s\n", runId, link.technologyName());
            }
            if (seq != frames::kProbeSeq) {
                g_received++;
            }
            frames::buildPong(out, len, seq, runId, g_received, message->rssiDbm, message->snrDb);
            reply(link, message->source, out, len);   // same size as the ping
            break;
        case 'C': {
            if (!isLoRa || in.size() < frames::kSwitchBytes) {
                break;
            }
            LoRaParams point{in[frames::kBwKHz], in[frames::kSf], in[frames::kCr],
                             static_cast<int8_t>(in[frames::kPower]), in[frames::kPreamble]};
            if (!acceptable(point)) {
                break;
            }
            frames::build(out, frames::kHeaderBytes, 'A', 0, runId);
            reply(link, message->source, out, frames::kHeaderBytes);   // acked on home, then move
            g_leaseMs = in[frames::kLease4s] * 4000UL;
            tuneLoRa(point, true);
            break;
        }
        case 'R':
            if (!isLoRa) {
                break;
            }
            frames::build(out, frames::kHeaderBytes, 'K', 0, runId);
            reply(link, message->source, out, frames::kHeaderBytes);
            if (g_leased) {
                tuneLoRa(kHome, false);
            }
            break;
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
