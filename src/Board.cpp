#include "Board.hpp"

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <XPowersLib.h>

namespace bench::board {

namespace {
XPowersAXP2101 g_pmu(Wire);
}   // namespace

void begin() {
    // Both rails are switched explicitly: the AXP2101 keeps its rail state across an ESP32 reset,
    // so whatever ran before would otherwise decide what is powered now. ALDO3 feeds the GPS,
    // which this firmware never uses; tens of milliamps of it would sit under every figure.
    Wire.begin(21, 22);
    if (!g_pmu.begin(Wire, AXP2101_SLAVE_ADDRESS, 21, 22)) {
        Serial.println("ERR,pmu not found");
    }
    g_pmu.disableALDO3();
    loRaRail(false);
    wifiOff();
}

void loRaRail(bool on) {
    if (on) {
        g_pmu.setALDO2Voltage(3300);
        g_pmu.enableALDO2();
        delay(20);   // let the rail settle before the radio is reset and configured
    } else {
        // Release the radio's pins first, or they would feed the unpowered chip through its inputs.
        for (int pin : {5, 19, 27, 18, 23, 26}) {
            pinMode(pin, INPUT);
        }
        g_pmu.disableALDO2();
    }
}

void wifiOff() {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
}

}   // namespace bench::board
