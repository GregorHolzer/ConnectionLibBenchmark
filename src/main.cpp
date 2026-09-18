// ConnectionLibBenchmark device firmware.
//
// One image, all three backends, selected at run time by `CFG` commands arriving over USB serial.
// A 10h campaign visits several hundred configuration points and a rebuild plus two flashes costs
// ~80s, so build-time configuration would spend the campaign flashing rather than measuring. This
// needed no changes to ConnectionLib: constructing a fresh Transport re-runs setup(), and all
// three backends tear down cleanly.
//
// Protocol (line-based, 115200 baud):
//   PING                     -> PONG,<role>
//   INFO                     -> INFO,<key>,<value> ...   build/environment facts for the audit
//   CFG tech=lora sf=9 ...   -> ACK  |  ERR,<reason>      (also emits the PPH run header)
//   RUN                      -> PPD,... PPS,... RDY       (gateway only)
//   RESET                    -> ACK                       clears node-side counters
//
// The host configures the node first and the gateway second, and both must ACK before RUN.

#include <cstdint>
#include <cstring>
#include <memory>

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <XPowersLib.h>

#include <ConnectionLib/Transport.hpp>
#include <ConnectionLib/backends/espnow/EspNowTransport.hpp>
#include <ConnectionLib/backends/lora/LoRaTransport.hpp>
#include <ConnectionLib/backends/zigbee/ZigbeeTransport.hpp>

#if defined(BENCH_HAS_DISPLAY)
#include <U8g2lib.h>
#endif

#include "BenchConfig.hpp"
#include "BenchRunner.hpp"
#include "Conformance.hpp"

using namespace connectionlib;
using bench::BenchConfig;
using bench::Tech;

#if defined(BENCH_ROLE_GATEWAY)
static const char *kRole = "gateway";
#else
static const char *kRole = "node";
#endif

XPowersAXP2101 PMU(Wire);

#if defined(BENCH_HAS_DISPLAY)
U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE);
#endif

BenchConfig g_config;
std::unique_ptr<Transport> g_transport;
bool g_configured = false;
uint32_t g_pointsRun = 0;

// ---------------------------------------------------------------------------------------------
// Board bring-up
// ---------------------------------------------------------------------------------------------

// The T-Beam's SX1278 hangs off the AXP2101's ALDO2 rail, which is off at boot. Enabled once at
// startup rather than per-configuration: the rail takes time to settle, and toggling it between
// every LoRa point would add a confound the measurement cannot see.
void setupPower() {
    Wire.begin(21, 22);
    if (!PMU.begin(Wire, AXP2101_SLAVE_ADDRESS, 21, 22)) {
        Serial.println("INFO,pmu,not-found");
        return;
    }
    PMU.setALDO2Voltage(3300);
    PMU.enableALDO2();
    Serial.println("INFO,pmu,aldo2-enabled");
}

/**
 * @brief Force WiFi off unless ESP-NOW is the technology under test.
 *
 * ~EspNowTransport deinitialises ESP-NOW but leaves the WiFi radio running, which would leave a
 * 2.4GHz transmitter active beside every subsequent Zigbee run - the external-load confound of
 * practice P5. The fix belongs here rather than in the library, since an ordinary application may
 * well want its WiFi left alone.
 */
void setRadioIdleState(Tech tech) {
    if (tech == Tech::EspNow) {
        return;   // EspNowTransport::setup() brings WiFi up itself
    }
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
}

// ---------------------------------------------------------------------------------------------
// Command parsing
// ---------------------------------------------------------------------------------------------

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

void applyKeyValue(BenchConfig &config, const char *key, const char *value) {
    auto matchKey = [key](const char *name) { return strcmp(key, name) == 0; };

    if (matchKey("tech")) {
        if (strcmp(value, "espnow") == 0) config.tech = Tech::EspNow;
        else if (strcmp(value, "lora") == 0) config.tech = Tech::LoRa;
        else if (strcmp(value, "zigbee") == 0) config.tech = Tech::Zigbee;
        else config.tech = Tech::None;
    }
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

// ---------------------------------------------------------------------------------------------
// Transport construction
// ---------------------------------------------------------------------------------------------

std::unique_ptr<Transport> buildTransport(const BenchConfig &config) {
    switch (config.tech) {
        case Tech::EspNow: {
            EspNowConfig cfg;
            std::array<uint8_t, 6> mac{};
            std::copy(config.peerMac, config.peerMac + 6, mac.begin());
            cfg.peers.push_back(Address::mac(mac));
            cfg.wifiChannel = config.wifiChannel;
            cfg.encrypt = config.encrypt;
            cfg.sendTimeoutMs = config.sendTimeoutMs;
            return std::make_unique<EspNowTransport>(cfg);
        }
        case Tech::LoRa: {
            LoRaConfig cfg;   // duty cycle left at ConnectionLib's EU-legal default, never relaxed
            cfg.frequencyMHz = config.frequencyMHz;
            cfg.bandwidthKHz = config.bandwidthKHz;
            cfg.spreadingFactor = config.spreadingFactor;
            cfg.codingRate = config.codingRate;
            cfg.txPowerDbm = config.txPowerDbm;
            cfg.preambleLength = config.preambleLength;
#if defined(BENCH_ROLE_GATEWAY)
            cfg.nodeId = 1;
#else
            cfg.nodeId = 2;
#endif
            return std::make_unique<LoRaTransport>(cfg);
        }
        case Tech::Zigbee:
            return std::make_unique<ZigbeeTransport>(ZigbeeConfig{});
        case Tech::None:
            return nullptr;
    }
    return nullptr;
}

Address peerAddress(const BenchConfig &config) {
    switch (config.tech) {
        case Tech::EspNow: {
            std::array<uint8_t, 6> mac{};
            std::copy(config.peerMac, config.peerMac + 6, mac.begin());
            return Address::mac(mac);
        }
        case Tech::LoRa:
#if defined(BENCH_ROLE_GATEWAY)
            return Address::loRaNode(2);
#else
            return Address::loRaNode(1);
#endif
        case Tech::Zigbee:
            // Unicast by default. A ZB broadcast blocks ~1s inside send() waiting for its APS
            // status and the reply lands inside that window, so it is buffered and the round trip
            // measures 0ms. Broadcast stays available as a swept parameter precisely so that
            // difference can be quantified rather than assumed.
            if (config.broadcast || config.peerXbee64 == 0) {
                return Address::broadcast(AddressKind::XBee64_16);
            }
            return Address::xbee(config.peerXbee64);
        case Tech::None:
            break;
    }
    return Address::broadcast(AddressKind::Broadcast);
}

// ---------------------------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------------------------

const char *techName(Tech tech) {
    switch (tech) {
        case Tech::EspNow: return "espnow";
        case Tech::LoRa: return "lora";
        case Tech::Zigbee: return "zigbee";
        case Tech::None: return "none";
    }
    return "unknown";
}

/** @brief One `PPH,<key>,<value>` line per input parameter, recorded with the results. */
void emitRunHeader() {
    Serial.printf("PPH,technology,%s\n", techName(g_config.tech));
    Serial.printf("PPH,role,%s\n", kRole);
    Serial.printf("PPH,payloadBytes,%u\n", g_config.payloadBytes);
    Serial.printf("PPH,exchanges,%lu\n", static_cast<unsigned long>(g_config.exchanges));
    Serial.printf("PPH,spacingMs,%lu\n", static_cast<unsigned long>(g_config.spacingMs));
    Serial.printf("PPH,replyWaitMs,%lu\n", static_cast<unsigned long>(g_config.replyWaitMs));
    Serial.printf("PPH,wifi,%s\n", g_config.tech == Tech::EspNow ? "on" : "off");

    if (g_transport) {
        Serial.printf("PPH,technologyName,%s\n", g_transport->technologyName());
        Serial.printf("PPH,maxPayloadBytes,%u\n",
                       static_cast<unsigned>(g_transport->maxPayloadBytes()));
    }

    switch (g_config.tech) {
        case Tech::LoRa:
            Serial.println("PPH,band,433MHz");
            Serial.printf("PPH,frequencyMHz,%.3f\n", static_cast<double>(g_config.frequencyMHz));
            Serial.printf("PPH,bandwidthKHz,%.1f\n", static_cast<double>(g_config.bandwidthKHz));
            Serial.printf("PPH,spreadingFactor,%u\n", g_config.spreadingFactor);
            Serial.printf("PPH,codingRate,%u\n", g_config.codingRate);
            Serial.printf("PPH,txPowerDbm,%d\n", g_config.txPowerDbm);
            Serial.printf("PPH,preambleLength,%u\n", g_config.preambleLength);
            Serial.println("PPH,regulation,ETSI EN 300 220-2 / ERC-REC 70-03 h1.3");
            break;
        case Tech::EspNow:
            Serial.println("PPH,band,2.4GHz");
            Serial.printf("PPH,wifiChannel,%u\n", g_config.wifiChannel);
            Serial.printf("PPH,encrypt,%d\n", g_config.encrypt ? 1 : 0);
            Serial.printf("PPH,sendTimeoutMs,%lu\n",
                           static_cast<unsigned long>(g_config.sendTimeoutMs));
            Serial.println("PPH,regulation,ETSI EN 300 328 (adaptive, no duty-cycle limit)");
            break;
        case Tech::Zigbee:
            Serial.println("PPH,band,2.4GHz");
            Serial.printf("PPH,addressing,%s\n", g_config.broadcast ? "broadcast" : "unicast");
            Serial.println("PPH,regulation,ETSI EN 300 328 (adaptive, no duty-cycle limit)");
            break;
        case Tech::None:
            Serial.println("PPH,band,none");
            break;
    }
}

/** @brief Build and environment facts the audit needs; see docs/benchmarking-practices.md P3/P6. */
void emitInfo() {
    Serial.printf("INFO,role,%s\n", kRole);
#if defined(NDEBUG)
    Serial.println("INFO,ndebug,1");
#else
    Serial.println("INFO,ndebug,0");
#endif
#if defined(__OPTIMIZE__)
    Serial.println("INFO,optimized,1");
#else
    Serial.println("INFO,optimized,0");
#endif
    // Only defined when the build sets it; absent means the Arduino core's default of 0 (none),
    // which is what a release build should have.
#if defined(CORE_DEBUG_LEVEL)
    Serial.printf("INFO,coreDebugLevel,%d\n", CORE_DEBUG_LEVEL);
#else
    Serial.println("INFO,coreDebugLevel,0");
#endif
    Serial.println("INFO,timer,micros");
    Serial.println("INFO,timerResolutionUs,1");
    Serial.printf("INFO,cpuFreqMHz,%lu\n", static_cast<unsigned long>(getCpuFrequencyMhz()));
    Serial.printf("INFO,sdkVersion,%s\n", ESP.getSdkVersion());
    Serial.printf("INFO,freeHeap,%lu\n", static_cast<unsigned long>(ESP.getFreeHeap()));
    Serial.printf("INFO,pointsRun,%lu\n", static_cast<unsigned long>(g_pointsRun));
    Serial.println("INFO,end,ok");
}

#if defined(BENCH_HAS_DISPLAY)
void showStatus(const char *line) {
    char detail[32];
    snprintf(detail, sizeof(detail), "%s %uB", techName(g_config.tech), g_config.payloadBytes);
    char points[32];
    snprintf(points, sizeof(points), "points: %lu", static_cast<unsigned long>(g_pointsRun));

    display.clearBuffer();
    display.setFont(u8g2_font_6x10_tf);
    display.drawStr(0, 10, "ConnectionLib bench");
    display.drawStr(0, 24, kRole);
    display.drawStr(0, 38, detail);
    display.drawStr(0, 52, line);
    display.drawStr(70, 24, points);
    display.sendBuffer();
}
#else
void showStatus(const char *) {}
#endif

// ---------------------------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------------------------

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

    if (const char *reason = bench::checkConformance(config)) {
        Serial.printf("ERR,%s\n", reason);
        return;
    }

    // Tear the old transport down before building the new one: EspNowTransport routes its C
    // callbacks through a single static instance pointer, so two live instances would fight.
    g_transport.reset();
    g_config = config;
    setRadioIdleState(config.tech);

    if (config.tech != Tech::None) {
        g_transport = buildTransport(config);
        if (!g_transport || !g_transport->setup()) {
            Serial.printf("ERR,transport setup failed (code %d)\n",
                           g_transport ? g_transport->lastErrorCode() : -1);
            g_transport.reset();
            g_configured = false;
            showStatus("setup FAILED");
            return;
        }
    }

    bench::resetNodeCounters();
    g_configured = true;
    emitRunHeader();
    Serial.println("ACK");
    showStatus("ready");
}

void handleRun() {
    if (!g_configured) {
        Serial.println("ERR,not configured");
        return;
    }
    showStatus("running");

    if (g_config.tech == Tech::None) {
        bench::runNullMeasurement(g_config);
    } else {
#if defined(BENCH_ROLE_GATEWAY)
        bench::runGatewayPoint(*g_transport, peerAddress(g_config), g_config);
#else
        Serial.println("ERR,RUN is gateway-only; the node serves continuously");
        return;
#endif
    }

    g_pointsRun++;
    Serial.println("RDY");
    showStatus("done");
}

void handleLine(char *line) {
    while (*line == ' ' || *line == '\t') {
        line++;
    }
    if (*line == '\0') {
        return;
    }

    if (strncmp(line, "CFG", 3) == 0) {
        handleConfigure(line + 3);
    } else if (strcmp(line, "RUN") == 0) {
        handleRun();
    } else if (strcmp(line, "PING") == 0) {
        Serial.printf("PONG,%s\n", kRole);
    } else if (strcmp(line, "INFO") == 0) {
        emitInfo();
    } else if (strcmp(line, "RESET") == 0) {
        bench::resetNodeCounters();
        Serial.println("ACK");
    } else {
        Serial.println("ERR,unknown command");
    }
}

void setup() {
    Serial.begin(115200);
    delay(500);

    setupPower();
    setRadioIdleState(Tech::None);   // start with WiFi down; ESP-NOW brings it up when selected

#if defined(BENCH_HAS_DISPLAY)
    Wire.begin(21, 22);
    display.begin();
#endif
    showStatus("idle");

    Serial.printf("BENCH,%s,ready\n", kRole);
}

void loop() {
    static char buffer[256];
    static size_t length = 0;

    while (Serial.available()) {
        char c = static_cast<char>(Serial.read());
        if (c == '\n' || c == '\r') {
            if (length > 0) {
                buffer[length] = '\0';
                handleLine(buffer);
                length = 0;
            }
        } else if (length < sizeof(buffer) - 1) {
            buffer[length++] = c;
        }
    }

#if defined(BENCH_ROLE_NODE)
    // The node has no RUN step: once configured it answers whatever arrives, for as long as the
    // gateway keeps sending. Serial is still serviced above, so a new CFG lands between polls.
    if (g_configured && g_transport) {
        bench::nodeTick(*g_transport);
    }
#endif
}
