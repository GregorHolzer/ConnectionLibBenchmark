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
#include "raw/RawTransports.hpp"

using namespace connectionlib;
using bench::BenchConfig;
using bench::Tech;
using bench::Stack;

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
//
// ALDO3 (GPS) is explicitly *disabled*. The GPS is never initialised by this firmware, so it was
// only ever a constant load of tens of milliamps - which is fine for latency but ruinous for
// energy, where it sits under every measurement and compresses the per-technology differences the
// benchmark exists to resolve. Disabling has to be explicit rather than merely "not enabling":
// the AXP2101 keeps its rail state across an ESP32 reset, so a rail left on by whatever firmware
// ran previously stays on.
//
// Rail mapping (ALDO2 = LoRa, ALDO3 = GPS) comes from the tbeam_rails diagnostic, not from
// guesswork. The rail states are reported over INFO so the measured configuration is recorded
// with the results rather than assumed.
void setupPower() {
    Wire.begin(21, 22);
    if (!PMU.begin(Wire, AXP2101_SLAVE_ADDRESS, 21, 22)) {
        Serial.println("INFO,pmu,not-found");
        return;
    }
    PMU.setALDO2Voltage(3300);
    PMU.enableALDO2();
    PMU.disableALDO3();
    Serial.println("INFO,pmu,aldo2-enabled-aldo3-disabled");
}

/** @brief Every AXP2101 rail's state, so the measured board configuration is on the record. */
void emitRailState() {
    Serial.printf("INFO,railALDO1,%d:%u\n", PMU.isEnableALDO1() ? 1 : 0, PMU.getALDO1Voltage());
    Serial.printf("INFO,railALDO2,%d:%u\n", PMU.isEnableALDO2() ? 1 : 0, PMU.getALDO2Voltage());
    Serial.printf("INFO,railALDO3,%d:%u\n", PMU.isEnableALDO3() ? 1 : 0, PMU.getALDO3Voltage());
    Serial.printf("INFO,railALDO4,%d:%u\n", PMU.isEnableALDO4() ? 1 : 0, PMU.getALDO4Voltage());
    Serial.printf("INFO,railDC1,%d:%u\n", PMU.isEnableDC1() ? 1 : 0, PMU.getDC1Voltage());
#if defined(BENCH_HAS_DISPLAY)
    Serial.println("INFO,display,on");
#else
    Serial.println("INFO,display,off");
#endif
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

// ---------------------------------------------------------------------------------------------
// Transport construction
// ---------------------------------------------------------------------------------------------

std::unique_ptr<Transport> buildTransport(const BenchConfig &config) {
    // The raw arm short-circuits before any ConnectionLib backend is constructed. Everything
    // downstream - runGatewayPoint, the timing points, the emitted records - is unchanged, which
    // is what makes the library-versus-raw difference attributable to the library itself.
    if (config.stack == Stack::Raw) {
        return createRawTransport(config);
    }

    switch (config.tech) {
        case Tech::EspNow: {
            EspNowConfig cfg;
            std::array<uint8_t, 6> mac{};
            std::copy(config.peerMac, config.peerMac + 6, mac.begin());
            cfg.peers.push_back(Address::mac(mac));
            cfg.wifiChannel = config.wifiChannel;
            cfg.encrypt = config.encrypt;
            cfg.sendTimeoutMs = config.sendTimeoutMs;
            cfg.captureRssi = config.captureRssi;
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
        case Tech::Zigbee: {
            ZigbeeConfig cfg;
            cfg.captureRssi = config.captureRssi;
            return std::make_unique<ZigbeeTransport>(cfg);
        }
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
    // Recorded per point, not just per campaign: a rotation interleaves the two stacks, so the
    // header is the only place that says which one produced the records that follow.
    Serial.printf("PPH,stack,%s\n", g_config.stack == Stack::Raw ? "raw" : "lib");
    Serial.printf("PPH,rssiCapture,%d\n", g_config.captureRssi ? 1 : 0);
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
    emitRailState();
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

/**
 * @brief Hold the current configuration for a fixed time without exchanging anything.
 *
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
    if (!g_configured) {
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
        if (g_transport) {
            g_transport->poll();   // stay in the receive state a real run would be in
            polls++;
        }
        delay(1);
    }

    Serial.printf("PPI,%s,%lu,%lu\n", techName(g_config.tech),
                   static_cast<unsigned long>(millis() - start),
                   static_cast<unsigned long>(polls));
    Serial.println("RDY");
    showStatus("idle done");
}

// T-Beam v1.1 SX1278 pinout, mirroring ConnectionLib's LoRaPinConfig defaults. Duplicated rather
// than imported because RFCHECK deliberately owns the radio itself, with no library in the path.
constexpr int kLoRaSck = 5, kLoRaMiso = 19, kLoRaMosi = 27;
constexpr int kLoRaNss = 18, kLoRaReset = 23, kLoRaDio0 = 26;

/**
 * @brief `RFCHECK` - measure the 433 MHz noise floor, then key the PA.
 *
 * Exists because "the received signal is weak" is not a claim anyone should accept from a packet
 * RSSI alone. A packet RSSI is only meaningful against the noise floor it sits on: -119 dBm is a
 * dead link if the floor is -121, and a perfectly healthy one if the floor is -150. RadioLib can
 * report the instantaneous RSSI with no packet involved, which is exactly that floor.
 *
 * The transmit half is for the PPK2: keying the PA should show as a step of roughly 100 mA on the
 * supply. If the current does not move, the radio is not transmitting at all, and no amount of
 * antenna is going to help.
 *
 * LoRa only - the other two radios expose neither quantity.
 */
bool g_rfCheckUseRfo = false;

void handleRfCheck() {
    // Drives the SX1278 directly rather than reaching inside a Transport: the diagnostic has to
    // work regardless of which stack is loaded, and the radio is a shared resource, so whatever
    // holds it is torn down first.
    g_transport.reset();
    g_configured = false;

    SPIClass spi(HSPI);
    spi.begin(kLoRaSck, kLoRaMiso, kLoRaMosi, kLoRaNss);
    // SX127x::getMod() is protected, and reading the PA registers is the whole point of this
    // diagnostic, so a one-line subclass opens the door rather than patching the library.
    struct ProbeRadio : SX1278 {
        using SX1278::SX1278;
        Module *mod() { return getMod(); }
    };
    ProbeRadio radio(new Module(kLoRaNss, kLoRaDio0, kLoRaReset, RADIOLIB_NC, spi));

    int16_t begun = radio.begin(g_config.frequencyMHz, g_config.bandwidthKHz,
                                 g_config.spreadingFactor, g_config.codingRate,
                                 RADIOLIB_SX127X_SYNC_WORD, g_config.txPowerDbm,
                                 g_config.preambleLength);
    if (begun != RADIOLIB_ERR_NONE) {
        // A chip-not-found here means the radio's rail is down or SPI is miswired - which is a far
        // more useful answer than any RSSI number would have been.
        Serial.printf("ERR,RFCHECK radio.begin failed (%d)\n", static_cast<int>(begun));
        return;
    }
    radio.startReceive();
    Serial.printf("RF,config,%.3f,%.0f,%u,%u,%d\n", static_cast<double>(g_config.frequencyMHz),
                   static_cast<double>(g_config.bandwidthKHz), g_config.spreadingFactor,
                   g_config.codingRate, g_config.txPowerDbm);

    // Read the transmit chain straight out of the chip. Everything upstream reports success - the
    // modem times packets to the microsecond - yet the supply shows no PA draw at all, so the
    // question is what the registers actually contain rather than what the API claims.
    //   PA_CONFIG (0x09): bit 7 = PaSelect (1 = PA_BOOST, 0 = RFO), bits 3:0 = OutputPower
    //   PA_DAC    (0x4D): 0x07 = +20 dBm boost enabled, 0x04 = normal
    //   OCP       (0x0B): bit 5 = OcpOn, bits 4:0 = OcpTrim - an over-current trim set too low
    //                     throttles the PA to almost nothing, which looks exactly like this
    uint8_t paConfig = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_PA_CONFIG);
    uint8_t paDac = radio.mod()->SPIreadRegister(RADIOLIB_SX1278_REG_PA_DAC);
    uint8_t ocp = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_OCP);
    Serial.printf("RF,pa_config,0x%02X,paselect,%s,outputpower,%u\n", paConfig,
                   (paConfig & 0x80) ? "PA_BOOST" : "RFO", static_cast<unsigned>(paConfig & 0x0F));
    Serial.printf("RF,pa_dac,0x%02X\n", paDac);
    Serial.printf("RF,ocp,0x%02X,on,%d,trim_ma,%u\n", ocp, (ocp & 0x20) ? 1 : 0,
                   static_cast<unsigned>((ocp & 0x1F) <= 15 ? 45 + 5 * (ocp & 0x1F)
                                                             : 130 + 10 * ((ocp & 0x1F) - 15)));

    // Noise floor: instantaneous RSSI, no packet. Sampled repeatedly because a single reading
    // catches whatever happened to be on the air in that microsecond.
    float floorMin = 999.0f, floorMax = -999.0f, floorSum = 0.0f;
    const int kSamples = 40;
    for (int i = 0; i < kSamples; i++) {
        float value = radio.getRSSI(false, true);
        floorMin = std::min(floorMin, value);
        floorMax = std::max(floorMax, value);
        floorSum += value;
        delay(5);
    }
    Serial.printf("RF,noisefloor_dbm,%.1f,%.1f,%.1f\n", static_cast<double>(floorMin),
                   static_cast<double>(floorSum / kSamples), static_cast<double>(floorMax));

    // The SX1278 has two transmit pins and only one of them is bonded to the antenna on any given
    // module: PA_BOOST or RFO. RadioLib picks PA_BOOST unless told otherwise, and driving the pin
    // the board did not wire would give exactly what is measured here - correct packet timing,
    // success from transmit(), no supply current, and a signal ~100 dB below expectation from the
    // little that leaks across. So key the PA both ways and let the supply current decide which
    // pin this board actually uses.
    if (g_rfCheckUseRfo) {
        int16_t state = radio.setOutputPower(g_config.txPowerDbm, true);
        Serial.printf("RF,output_pin,RFO,setOutputPower,%d\n", static_cast<int>(state));
        uint8_t cfg = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_PA_CONFIG);
        Serial.printf("RF,pa_config_after,0x%02X,paselect,%s\n", cfg,
                       (cfg & 0x80) ? "PA_BOOST" : "RFO");
    } else {
        Serial.println("RF,output_pin,PA_BOOST");
    }

    // Transmit half: ten full-size packets, spaced so the PPK2 resolves each one as a step.
    uint8_t buffer[64];
    for (size_t i = 0; i < sizeof(buffer); i++) {
        buffer[i] = static_cast<uint8_t>(i);
    }
    Serial.println("RF,tx_begin,10");
    for (int i = 0; i < 10; i++) {
        uint32_t t0 = micros();
        int16_t state = radio.transmit(buffer, sizeof(buffer));
        uint32_t elapsed = micros() - t0;
        Serial.printf("RF,tx,%d,%d,%lu\n", i, static_cast<int>(state),
                       static_cast<unsigned long>(elapsed));
        delay(400);
    }
    radio.startReceive();
    Serial.println("RDY");
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
    } else if (strncmp(line, "IDLE", 4) == 0) {
        handleIdle(line + 4);
    } else if (strcmp(line, "RUN") == 0) {
        handleRun();
    } else if (strcmp(line, "PING") == 0) {
        Serial.printf("PONG,%s\n", kRole);
    } else if (strcmp(line, "RFCHECK") == 0) {
        g_rfCheckUseRfo = false;
        handleRfCheck();
    } else if (strcmp(line, "RFCHECK RFO") == 0) {
        g_rfCheckUseRfo = true;
        handleRfCheck();
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
