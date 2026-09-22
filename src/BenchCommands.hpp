/**
 * @brief The CFG / RUN / IDLE verbs of the serial protocol.
 *
 * A CFG is a complete specification, never a delta on the previous point: each one starts from
 * BenchConfig's defaults, so a campaign's points cannot contaminate one another through the
 * firmware's state. IDLE is the standing-cost measurement, which for a node that must last months
 * may matter more than the active one.
 */

#pragma once

#include "BenchConfig.hpp"

namespace bench {

/** @brief Apply one `key=value` token to a config. Unknown keys are ignored. */
void applyKeyValue(BenchConfig &config, const char *key, const char *value);

/** @brief `CFG ...` - parse, validate, rebuild the transport, ACK or ERR. */
void handleConfigure(char *args);

/** @brief `RUN` - execute one configuration point. Gateway only. */
void handleRun();

/** @brief `IDLE <ms>` - hold the configuration without exchanging, for the energy window. */
void handleIdle(char *args);

}   // namespace bench
