#pragma once

#include "BenchConfig.hpp"

namespace bench {

/**
 * @brief Rejects any configuration that could not legally be transmitted in the EU.
 *
 * Enforced on the device rather than only in the host sweep definitions, so that a hand-typed
 * `CFG` command or a future sweep cannot accidentally put the radio outside its envelope. The
 * benchmark must measure a radio that can actually be deployed (docs/benchmarking-practices.md
 * P7); raising power or widening bandwidth to make a run finish sooner would measure something
 * nobody may legally build.
 *
 * @param config the parsed configuration
 * @return nullptr if the configuration is legal, otherwise a short reason for the `ERR` reply
 */
const char *checkConformance(const BenchConfig &config);

}   // namespace bench
