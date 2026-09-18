#pragma once

#include <cstdint>

#include <ConnectionLib/Transport.hpp>

#include "BenchConfig.hpp"

// The measured exchange loop. Ported from ConnectionLib-examples' pingpong/src/PingPong.cpp,
// keeping its corrected timing and generalised from build-time macros to a runtime BenchConfig.
//
// Wire format: [0] kind ('P' ping / 'Q' pong), [1..2] little-endian uint16 sequence number,
// [3..] deterministic padding to the configured size. A fixed record rather than a formatted
// string, because payload size is a swept input parameter (practice P8) and "ping #42" cannot be
// made to hit an exact byte count.
namespace bench {

constexpr size_t kMinPayloadBytes = 3;
constexpr size_t kMaxPayloadBytes = 250;   // ESP-NOW's limit, the largest of the three

/**
 * @brief Gateway role: run one complete configuration point, blocking until it finishes.
 *
 * Strict request/response - never more than one ping in flight, and ping N+1 is not sent until
 * ping N is answered. Emits one `PPD` record per attempt and a `PPS` summary at the end.
 *
 * Timing is taken from *before* send() to the instant the matching reply comes back from poll(),
 * and reported as two separate figures (`sendUs`, `rttUs`). Taking it afterwards - as the original
 * harness did - silently excludes the entire outbound transmission; see docs/metrics.md.
 */
void runGatewayPoint(connectionlib::Transport &transport, const connectionlib::Address &peer,
                     const BenchConfig &config);

/**
 * @brief Node role: service at most one incoming ping per call. Non-blocking; call from loop().
 *
 * Counts *distinct* sequence numbers, which is what makes receiver-side PDR measurable: the
 * sender's view of delivery means three different things across these three radios.
 */
void nodeTick(connectionlib::Transport &transport);

/** @brief Clear node-side counters. Called when a new configuration point is configured. */
void resetNodeCounters();

/**
 * @brief Null measurement: the harness's own per-exchange cost, with no radio involved.
 *
 * Practice P4 - the measurement overhead has to be quantified before any latency figure can be
 * trusted, and this is the number the audit compares against the smallest reported latency.
 */
void runNullMeasurement(const BenchConfig &config);

}   // namespace bench
