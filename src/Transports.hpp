/**
 * @brief Building the transport for a technology, and the address of the other board.
 *
 * Transports.cpp is the only file that names a backend. ConnectionLibBenchmarkRaw replaces it with
 * one that builds the raw vendor-API transports; node.cpp and gateway.cpp are the same in both.
 */
#pragma once

#include <memory>

#include <ConnectionLib/Transport.hpp>

#include "Config.hpp"

namespace bench {

/** @brief ackTimeoutMs: how long send() waits for the library's ack at these parameters. */
std::unique_ptr<connectionlib::Transport> makeLoRa(const LoRaParams &params, uint32_t ackTimeoutMs);
std::unique_ptr<connectionlib::Transport> makeEspNow();
std::unique_ptr<connectionlib::Transport> makeZigbee();

/** @brief The one transport a run uses: `tech`, and for LoRa these parameters. nullptr for None. */
std::unique_ptr<connectionlib::Transport> makeTransport(Tech tech, const LoRaParams &lora,
                                                        uint32_t loRaAckTimeoutMs);

/** @brief Where the node sends. The gateway replies to whatever source it heard. */
connectionlib::Address gatewayAddress(Tech tech);

}   // namespace bench
