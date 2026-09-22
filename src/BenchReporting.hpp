/**
 * @brief The lines the firmware emits about itself: the run header and the build/environment facts.
 *
 * Both exist so the analysis never has to assume what the board was doing. `PPH,` lines are
 * written per configuration point and end up as the point's recorded inputs; `INFO,` lines answer
 * the audit's questions about the build (practices P3 and P6).
 */

#pragma once

#include "BenchConfig.hpp"

namespace bench {

/** @brief Enum to the name used in records and on the wire protocol. */
const char *techName(Tech tech);

/** @brief One `PPH,<key>,<value>` line per input parameter, recorded with the results. */
void emitRunHeader();

/** @brief Build and environment facts the audit needs; see docs/benchmarking-practices.md P3/P6. */
void emitInfo();

}   // namespace bench
