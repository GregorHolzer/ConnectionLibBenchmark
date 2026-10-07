/** @brief What must be true of the T-Beam before a measurement means anything. */
#pragma once

namespace bench::board {

/**
 * @brief Power the LoRa rail, cut the GPS rail, and optionally force WiFi off.
 *
 * WiFi is forced off for everything except ESP-NOW so that no second 2.4 GHz radio sits under a
 * LoRa or Zigbee energy figure.
 */
void powerUp(bool wifiOff);

}   // namespace bench::board
