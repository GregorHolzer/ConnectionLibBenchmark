/** @brief What must be true of the T-Beam before a measurement means anything. */
#pragma once

namespace bench::board {

/** @brief Bring the PMU up with the GPS rail and the LoRa rail off, and WiFi off. */
void begin();

/**
 * @brief Power the SX1278 (AXP2101 ALDO2) or cut it.
 *
 * Cut whenever LoRa is not the radio in use: a LoRaTransport that is merely destroyed leaves the
 * chip in continuous receive, which would sit under every ESP-NOW and Zigbee energy figure.
 */
void loRaRail(bool on);

/** @brief Switch WiFi off again after an ESP-NOW transport has been destroyed. */
void wifiOff();

}   // namespace bench::board
