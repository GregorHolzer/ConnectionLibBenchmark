/** @brief Implementation of the CFG / RUN / IDLE verbs. See BenchCommands.hpp. */

#include "BenchCommands.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <Arduino.h>

#include "BenchDisplay.hpp"
#include "BenchReporting.hpp"
#include "BenchRunner.hpp"
#include "BenchPower.hpp"
#include "BenchState.hpp"
#include "Conformance.hpp"
#include "TransportFactory.hpp"

namespace bench {

namespace {

bool parseMac(const char *text, uint8_t out[6]) {
    unsigned values[6];
    if (sscanf(text, "%x:%x:%x:%x:%x:%x", &values[0], &values[1], &values[2], &values[3],
                &values[4], &values[5]) != 6) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        out[i] = static_cast<uint8_t>(values[i]);
    }
    return true;
}

}   // namespace

void applyKeyValue(BenchConfig &config, const char *key, const char *value) {
    auto matchKey = [key](const char *name) { return strcmp(key, name) == 0; };

    if (matchKey("tech")) {
        if (strcmp(value, "espnow") == 0) config.tech = Tech::EspNow;
        else if (strcmp(value, "lora") == 0) config.tech = Tech::LoRa;
        else if (strcmp(value, "zigbee") == 0) config.tech = Tech::Zigbee;
        else config.tech = Tech::None;
    }
    else if (matchKey("stack")) config.stack = (strcmp(value, "raw") == 0) ? Stack::Raw : Stack::Library;
    else if (matchKey("rssi")) config.captureRssi = (atoi(value) != 0);
    else if (matchKey("payload")) config.payloadBytes = atoi(value);
    else if (matchKey("n")) config.exchanges = strtoul(value, nullptr, 10);
    else if (matchKey("spacing")) config.spacingMs = strtoul(value, nullptr, 10);
    else if (matchKey("replywait")) config.replyWaitMs = strtoul(value, nullptr, 10);
    else if (matchKey("freq")) config.frequencyMHz = atof(value);
    else if (matchKey("bw")) config.bandwidthKHz = atof(value);
    else if (matchKey("sf")) config.spreadingFactor = atoi(value);
    else if (matchKey("cr")) config.codingRate = atoi(value);
    else if (matchKey("pwr")) config.txPowerDbm = atoi(value);
    else if (matchKey("preamble")) config.preambleLength = atoi(value);
    else if (matchKey("channel")) config.wifiChannel = atoi(value);
    else if (matchKey("encrypt")) config.encrypt = atoi(value) != 0;
    else if (matchKey("timeout")) config.sendTimeoutMs = strtoul(value, nullptr, 10);
    else if (matchKey("broadcast")) config.broadcast = atoi(value) != 0;
    else if (matchKey("peermac")) parseMac(value, config.peerMac);
    else if (matchKey("peerxbee")) config.peerXbee64 = strtoull(value, nullptr, 16);
}

void handleConfigure(char *args) {
    BenchConfig config;   // start from defaults every time: a CFG is a complete specification,
                          // never a delta on the previous point
    for (char *token = strtok(args, " \t"); token != nullptr; token = strtok(nullptr, " \t")) {
        char *equals = strchr(token, '=');
        if (!equals) {
            continue;
        }
        *equals = '\0';
        applyKeyValue(config, token, equals + 1);
    }

    applyConfiguration(config, true);
}

bool applyConfiguration(const BenchConfig &config, bool announce) {
    State &s = state();
    if (const char *reason = checkConformance(config)) {
        if (announce) {
            Serial.printf("ERR,%s\n", reason);
        }
        return false;
    }

    // Tear the old transport down before building the new one: EspNowTransport routes its C
    // callbacks through a single static instance pointer, so two live instances would fight.
    s.transport.reset();
    s.config = config;
    setRadioIdleState(config.tech);

    if (config.tech != Tech::None) {
        s.transport = buildTransport(config);
        if (!s.transport || !s.transport->setup()) {
            if (announce) {
                Serial.printf("ERR,transport setup failed (code %d)\n",
                               s.transport ? s.transport->lastErrorCode() : -1);
            }
            s.transport.reset();
            s.configured = false;
            showStatus("setup FAILED");
            return false;
        }
    }

    resetNodeCounters();
    s.configured = true;
    if (announce) {
        emitRunHeader();
        Serial.println("ACK");
    }
    showStatus("ready");
    return true;
}

void handleRun() {
    State &s = state();
    if (!s.configured) {
        Serial.println("ERR,not configured");
        return;
    }
    showStatus("running");

    if (s.config.tech == Tech::None) {
        runNullMeasurement(s.config);
    } else {
#if defined(BENCH_ROLE_GATEWAY)
        runGatewayPoint(*s.transport, peerAddress(s.config), s.config);
#else
        Serial.println("ERR,RUN is gateway-only; the node serves continuously");
        return;
#endif
    }

    s.pointsRun++;
    Serial.println("RDY");
    showStatus("done");
}

/**
 * This is the standing-cost measurement, and it may matter more than the active one. A node that
 * must live months on an 18650 spends nearly all its time not transmitting, so what the radio
 * costs while merely *able* to receive dominates the energy budget - and the three differ
 * enormously there: ESP-NOW cannot receive without WiFi up, LoRa sits in continuous receive, the
 * XBee idles on its own. A technology can lose the energy comparison outright on idle current no
 * matter how efficient it is per packet, and only this measurement would show it.
 *
 * The node keeps polling during the hold so it stays in exactly the receive state it uses during a
 * real run; poll() is non-blocking on all three backends, so this measures the idle radio rather
 * than an artificially quiescent one.
 */
void handleIdle(char *args) {
    State &s = state();
    if (!s.configured) {
        Serial.println("ERR,not configured");
        return;
    }
    uint32_t durationMs = strtoul(args, nullptr, 10);
    if (durationMs == 0 || durationMs > 600000) {
        Serial.println("ERR,IDLE needs a duration in ms, 1..600000");
        return;
    }

    showStatus("idle hold");
    uint32_t start = millis();
    uint32_t polls = 0;
    while (millis() - start < durationMs) {
        if (s.transport) {
            s.transport->poll();   // stay in the receive state a real run would be in
            polls++;
        }
        delay(1);
    }

    Serial.printf("PPI,%s,%lu,%lu\n", techName(s.config.tech),
                   static_cast<unsigned long>(millis() - start),
                   static_cast<unsigned long>(polls));
    Serial.println("RDY");
    showStatus("idle done");
}

}   // namespace bench
