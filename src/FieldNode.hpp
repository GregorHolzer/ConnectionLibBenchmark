/**
 * @brief The field node's half of the lease protocol: the two serial verbs that reach the gateway.
 *
 *   LEASE cid=<hex> pt=<n> lease=<s> <CFG tokens>   -> LEASE,ok,<rssi>,<snr> PPH,... ACK
 *                                                     | ERR,lease noack | ERR,lease rejected | ERR,...
 *   RELEASE                                         -> RELEASE,ok|noack RDY
 *
 * LEASE sends the point's configuration to the gateway over home, waits for its ack, and then
 * configures this board exactly as CFG would - so RUN afterwards is the bench's RUN, unchanged.
 * RELEASE sends the gateway back to home once RUN has finished, so the next point doesn't have to
 * wait out the whole lease. See docs/field-campaign.md.
 */

#pragma once

namespace bench::field {

void handleLease(char *args);
void handleRelease();

}   // namespace bench::field
