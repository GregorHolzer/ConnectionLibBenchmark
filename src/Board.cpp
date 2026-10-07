#include "Board.hpp"

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <XPowersLib.h>

namespace bench::board {

void powerUp(bool wifiOff) {
    // ALDO2 feeds the SX1278 and is off at boot. ALDO3 feeds the GPS, which this firmware never
    // uses: it has to be switched off explicitly, because the AXP2101 keeps its rail state across
    // an ESP32 reset and tens of milliamps of GPS would sit under every energy figure.
    static XPowersAXP2101 pmu(Wire);
    Wire.begin(21, 22);
    if (pmu.begin(Wire, AXP2101_SLAVE_ADDRESS, 21, 22)) {
        pmu.setALDO2Voltage(3300);
        pmu.enableALDO2();
        pmu.disableALDO3();
    } else {
        Serial.println("ERR,pmu not found");
    }
    if (wifiOff) {
        WiFi.disconnect(true);
        WiFi.mode(WIFI_OFF);
    }
}

}   // namespace bench::board
