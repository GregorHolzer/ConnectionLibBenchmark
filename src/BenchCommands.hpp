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

/**
 * @brief Validate a parsed config and rebuild the transport around it.
 *
 * The body of CFG, shared with the field mode's lease path. `announce` prints the PPH header and
 * ACK/ERR for the host; the field gateway has no host, and the field node's LEASE builds its home
 * transport silently so the only ACK the host reads is the one for the point itself.
 */
bool applyConfiguration(const BenchConfig &config, bool announce);

/** @brief `RUN` - execute one configuration point. Gateway only. */
void handleRun();

/** @brief `IDLE <ms>` - hold the configuration without exchanging, for the energy window. */
void handleIdle(char *args);

}   // namespace bench
