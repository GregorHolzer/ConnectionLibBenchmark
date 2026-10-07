/**
 * @brief The node's side of everything that is not the measured loop: waiting for a frame, and the
 * LoRa handshake that moves the gateway to a point and back (see gateway.cpp).
 */
#pragma once

#include <memory>
#include <optional>

#include <ConnectionLib/Transport.hpp>

namespace bench {

constexpr int kControlAttempts = 3;

/**
 * @brief Wait up to waitMs for a frame of `kind` with this sequence number from this run.
 *
 * listen(0) in a 200 us loop, not listen(waitMs): the library's blocking listen() sleeps in 1 ms
 * steps, which would quantise ESP-NOW's ~3.5 ms round trip into the harness's own ticks.
 */
std::optional<connectionlib::ReceivedMessage> await(connectionlib::Transport &link, char kind,
                                                    uint16_t seq, uint32_t waitMs);

/**
 * @brief Tell the gateway, on home, which LoRa point comes next, then move `link` to that point.
 *
 * A missing ack is ambiguous: either the request was lost and the gateway is still on home, or
 * only the ack was lost and it has already moved. So each attempt ends with a probe on the point
 * itself, and an answer there counts as success. Prints HOME,ok,... or HOME,noack.
 */
bool negotiate(std::unique_ptr<connectionlib::Transport> &link);

/** @brief Send the gateway back to home now, instead of letting it wait out the lease. */
void release(connectionlib::Transport &link);

}   // namespace bench
