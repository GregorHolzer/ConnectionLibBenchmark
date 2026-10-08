/**
 * @brief The node's side of everything around the measured loop: telling the gateway which run
 * comes next, and ending it (see gateway.cpp).
 */
#pragma once

#include <memory>

#include <ConnectionLib/Transport.hpp>

namespace bench {

/**
 * @brief Announce this build's configuration to the gateway on LoRa home, then bring up the
 * run's own transport in `link`.
 *
 * The announcement is confirmed by the library's ack. A missing ack is ambiguous: either the
 * frame was lost and the gateway is still on home, or only the ack was lost and it has already
 * switched. So each attempt ends with a probe on the run's radio, and an ack there counts as
 * success. Prints HOME,ok,<attempts> or HOME,noack.
 */
bool announce(std::unique_ptr<connectionlib::Transport> &link);

/**
 * @brief After the run: tell the gateway it is over, which sends it back to home, and take its
 * report.
 *
 * Prints REPORT,ok,<received>,<rssi>,<snr>, REPORT,lost (the end message was acked, so the
 * gateway is back on home, but its report did not arrive), or REPORT,noack.
 */
void endRun(connectionlib::Transport &link);

}   // namespace bench
