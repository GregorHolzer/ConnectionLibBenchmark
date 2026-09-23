/** @brief Rail bring-up and radio idle state. See BenchPower.hpp for why the two sit together. */

#include "BenchPower.hpp"

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <XPowersLib.h>

namespace bench {

namespace {
XPowersAXP2101 PMU(Wire);
}   // namespace

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

}   // namespace bench
