/**
 * @brief The ping-pong wire format, and the node-side counters both roles touch.
 *
 * Shared by BenchRunner.cpp (gateway) and BenchNode.cpp (node), which is the only reason these are
 * a header rather than an anonymous namespace. Nothing here talks to a radio.
 *
 * Wire format: [0] kind ('P' ping / 'Q' pong), [1..2] little-endian uint16 sequence number,
 * [3..] deterministic padding to the configured size. A fixed record rather than a formatted
 * string, because payload size is a swept input parameter (practice P8) and "ping #42" cannot be
 * made to hit an exact byte count.
 */

#pragma once

#include <cstdint>
#include <optional>
#include <vector>

#include <ConnectionLib/LinkMetrics.hpp>
#include <ConnectionLib/Transport.hpp>

#include "BenchConfig.hpp"
#include "BenchRunner.hpp"

namespace bench::detail {

// Poll granularity while waiting for a reply. The original harness used delay(5), which put a hard
// 5ms floor under every latency figure - every ESP-NOW exchange read exactly 5.00ms, i.e. the
// harness was being reported rather than the radio. 200us resolves ESP-NOW comfortably while still
// yielding to the ESP32's WiFi task.
constexpr uint32_t kPollIntervalUs = 200;

/** @brief Receiver-side delivery counters, reset per configuration point. */
struct NodeCounters {
    uint32_t rxTotal = 0;
    uint32_t rxDistinct = 0;
    uint32_t repliesOk = 0;
    uint32_t replyFailures = 0;
};

/** @brief The single instance, shared by resetNodeCounters() and nodeTick(). */
NodeCounters &counters();

/** @brief Clear the distinct-arrival bitmap. */
void resetSeen();

/** @brief Record a sequence number; false if it had already arrived. */
bool markSeen(uint16_t seq);

void buildMessage(uint8_t *buf, size_t len, char kind, uint16_t seq);

uint16_t sequenceOf(const std::vector<uint8_t> &payload);

bool matches(const connectionlib::ReceivedMessage &msg, char kind, uint16_t seq, size_t len);

/** @brief CSV field for an optional metric: the value, or empty where the radio can't report it. */
void printOptional(const std::optional<float> &value);

size_t effectivePayloadBytes(const connectionlib::Transport &transport, const BenchConfig &config);

}   // namespace bench::detail
