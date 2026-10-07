/**
 * @brief The node's side of everything around the measured loop: moving the gateway to a LoRa
 * point, and collecting its report afterwards (see gateway.cpp).
 */
#pragma once

#include <memory>

#include <ConnectionLib/Transport.hpp>

namespace bench {

/**
 * @brief Tell the gateway, on home, which LoRa point comes next, then move `link` to that point.
 *
 * The switch frame is confirmed by the library's ack. A missing ack is ambiguous: either the
 * frame was lost and the gateway is still on home, or only the ack was lost and it has already
 * moved. So each attempt ends with a probe on the point itself, and an ack there counts as
 * success. Prints HOME,ok,<attempts> or HOME,noack.
 */
bool negotiate(std::unique_ptr<connectionlib::Transport> &link);

/**
 * @brief After the run: ask the gateway what it received, which on LoRa also sends it home.
 *
 * Prints REPORT,ok,<received>,<rssi>,<snr>, REPORT,lost (the request was acked, so the gateway is
 * released, but its report did not arrive), or REPORT,noack.
 */
void collectReport(connectionlib::Transport &link);

}   // namespace bench
