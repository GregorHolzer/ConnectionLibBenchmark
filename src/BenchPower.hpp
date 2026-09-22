/**
 * @brief T-Beam power rails and the WiFi idle state - everything that must be true of the board
 * before a measurement means anything.
 *
 * Both jobs here exist to remove a confound rather than to make the radio work, which is why they
 * live together: one keeps a rail from sitting under every energy figure, the other keeps a second
 * transmitter from sitting beside every latency figure (practice P5).
 */

#pragma once

#include "BenchConfig.hpp"

namespace bench {

/** @brief Bring up the LoRa rail and explicitly shut down the GPS rail. */
void setupPower();

/** @brief Every AXP2101 rail's state, so the measured board configuration is on the record. */
void emitRailState();

/** @brief Force WiFi off unless ESP-NOW is the technology under test. */
void setRadioIdleState(Tech tech);

}   // namespace bench
