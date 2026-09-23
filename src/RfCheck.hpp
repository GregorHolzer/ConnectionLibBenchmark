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
 * A diagnostic, not a measurement: it drives the SX1278 directly with no ConnectionLib in the
 * path, and tears down whatever transport was loaded because the radio is a shared resource.
 * LoRa only - the other two radios expose neither quantity.
 */

#pragma once

namespace bench {

/** @brief Run the diagnostic. `useRfo` keys the RFO pin instead of PA_BOOST. */
void handleRfCheck(bool useRfo);

}   // namespace bench
