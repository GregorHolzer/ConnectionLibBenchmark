/**
 * @brief The firmware's one live configuration point, shared by the command handlers.
 *
 * main.cpp used to hold these as file-scope globals. They are still one instance and still
 * global in lifetime - a benchmark board runs exactly one configuration at a time - but going
 * through a named struct keeps the split files honest about what they touch, which a header
 * full of `extern` declarations would not.
 *
 * `transport` is null whenever the technology is None (the null measurement, practice P4) or
 * setup() failed, so every user checks it.
 */

#pragma once

#include <cstdint>
#include <memory>

#include <ConnectionLib/Transport.hpp>

#include "BenchConfig.hpp"

namespace bench {

struct State {
    BenchConfig config;
    std::unique_ptr<connectionlib::Transport> transport;
    bool configured = false;
    uint32_t pointsRun = 0;
};

/** @brief The single instance. Defined in BenchState.cpp. */
State &state();

/**
 * @brief "gateway" or "node", fixed at build time by BENCH_ROLE_*, or "field-gateway" /
 * "field-node" under BENCH_FIELD_*.
 *
 * BENCH_ROLE_* is the board's part in the exchange - GATEWAY runs the measured loop, NODE answers -
 * and on the bench that coincides with the board's name. In the field it doesn't: the field node
 * carries the laptop and runs the loop, so it builds with BENCH_ROLE_GATEWAY, and the field gateway
 * answers, so it builds with BENCH_ROLE_NODE. See platformio.ini.
 */
const char *role();

}   // namespace bench
