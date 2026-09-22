/** @brief Emitting the run header and the build facts. See BenchReporting.hpp. */

#include "BenchReporting.hpp"

#include <Arduino.h>

#include "BenchPower.hpp"
#include "BenchState.hpp"

namespace bench {

const char *techName(Tech tech) {
    switch (tech) {
        case Tech::EspNow: return "espnow";
        case Tech::LoRa: return "lora";
        case Tech::Zigbee: return "zigbee";
        case Tech::None: return "none";
    }
    return "unknown";
}

void emitRunHeader() {
    State &s = state();
    Serial.printf("PPH,technology,%s\n", techName(s.config.tech));
    Serial.printf("PPH,role,%s\n", role());
    // Recorded per point, not just per campaign: a rotation interleaves the two stacks, so the
    // header is the only place that says which one produced the records that follow.
    Serial.printf("PPH,stack,%s\n", s.config.stack == Stack::Raw ? "raw" : "lib");
    Serial.printf("PPH,rssiCapture,%d\n", s.config.captureRssi ? 1 : 0);
    Serial.printf("PPH,payloadBytes,%u\n", s.config.payloadBytes);
    Serial.printf("PPH,exchanges,%lu\n", static_cast<unsigned long>(s.config.exchanges));
    Serial.printf("PPH,spacingMs,%lu\n", static_cast<unsigned long>(s.config.spacingMs));
    Serial.printf("PPH,replyWaitMs,%lu\n", static_cast<unsigned long>(s.config.replyWaitMs));
    Serial.printf("PPH,wifi,%s\n", s.config.tech == Tech::EspNow ? "on" : "off");

    if (s.transport) {
        Serial.printf("PPH,technologyName,%s\n", s.transport->technologyName());
        Serial.printf("PPH,maxPayloadBytes,%u\n",
                       static_cast<unsigned>(s.transport->maxPayloadBytes()));
    }

    switch (s.config.tech) {
        case Tech::LoRa:
            Serial.println("PPH,band,433MHz");
            Serial.printf("PPH,frequencyMHz,%.3f\n", static_cast<double>(s.config.frequencyMHz));
            Serial.printf("PPH,bandwidthKHz,%.1f\n", static_cast<double>(s.config.bandwidthKHz));
            Serial.printf("PPH,spreadingFactor,%u\n", s.config.spreadingFactor);
            Serial.printf("PPH,codingRate,%u\n", s.config.codingRate);
            Serial.printf("PPH,txPowerDbm,%d\n", s.config.txPowerDbm);
            Serial.printf("PPH,preambleLength,%u\n", s.config.preambleLength);
            Serial.println("PPH,regulation,ETSI EN 300 220-2 / ERC-REC 70-03 h1.3");
            break;
        case Tech::EspNow:
            Serial.println("PPH,band,2.4GHz");
            Serial.printf("PPH,wifiChannel,%u\n", s.config.wifiChannel);
            Serial.printf("PPH,encrypt,%d\n", s.config.encrypt ? 1 : 0);
            Serial.printf("PPH,sendTimeoutMs,%lu\n",
                           static_cast<unsigned long>(s.config.sendTimeoutMs));
            Serial.println("PPH,regulation,ETSI EN 300 328 (adaptive, no duty-cycle limit)");
            break;
        case Tech::Zigbee:
            Serial.println("PPH,band,2.4GHz");
            Serial.printf("PPH,addressing,%s\n", s.config.broadcast ? "broadcast" : "unicast");
            Serial.println("PPH,regulation,ETSI EN 300 328 (adaptive, no duty-cycle limit)");
            break;
        case Tech::None:
            Serial.println("PPH,band,none");
            break;
    }
}

void emitInfo() {
    Serial.printf("INFO,role,%s\n", role());
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
    Serial.printf("INFO,pointsRun,%lu\n", static_cast<unsigned long>(state().pointsRun));
    emitRailState();
    Serial.println("INFO,end,ok");
}

}   // namespace bench
