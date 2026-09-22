/**
 * @brief Turning a BenchConfig into a live Transport, and into the address to send to.
 *
 * The one place that knows about all three backends at once. Both functions branch on
 * `config.stack` / `config.tech` and nothing downstream does - runGatewayPoint, the timing points
 * and the emitted records are identical for every technology and both arms, which is what makes
 * the library-versus-raw difference attributable to the library rather than to the harness.
 */

#pragma once

#include <memory>

#include <ConnectionLib/Address.hpp>
#include <ConnectionLib/Transport.hpp>

#include "BenchConfig.hpp"

namespace bench {

/** @brief The configured transport, or null for Tech::None (the null measurement). */
std::unique_ptr<connectionlib::Transport> buildTransport(const BenchConfig &config);

/** @brief The address this board should send to, in the form its radio addresses with. */
connectionlib::Address peerAddress(const BenchConfig &config);

}   // namespace bench
