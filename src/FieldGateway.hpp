/**
 * @brief The field gateway: a responder with no host, configured over the air one lease at a time.
 *
 * It boots into the home configuration and waits for a lease request. On one it acks over home,
 * switches to the point's configuration and answers pings, then goes back to home when the node
 * releases it or the lease expires - whichever comes first, and whether the point worked or not.
 * A reboot lands on home too, and the task watchdog turns a hang into a reboot, so no single
 * failure can leave it somewhere the node can't reach.
 *
 * Node->gateway delivery can only be counted here, and nothing reads this board's serial port in
 * the field, so each lease ends with one record appended to LittleFS. `LOG` prints them once the
 * board has been collected; tools/field_gateway.py turns them back into node_rx.csv.
 */

#pragma once

namespace bench::field {

/** @brief Mount the log, record the boot, start the watchdog and go to home. Call from setup(). */
void gatewaySetup();

/** @brief One pass of the state machine. Non-blocking apart from sends; call from loop(). */
void gatewayTick();

/** @brief `LOG` - print every stored record, then `LOG,end,<count>`. */
void handleLogDump();

/** @brief `LOGCLEAR` - delete the stored records. */
void handleLogClear();

/** @brief Bytes currently in the log, for INFO. */
unsigned long logBytes();

}   // namespace bench::field
