/**
 * @brief One configuration point, fixed at build time.
 *
 * bench.py writes include/bench_config.h and re-flashes the node for every point, so there is no
 * serial command protocol: the firmware boots, measures what it was built for, and stops. Without
 * that header the defaults below build an idle board, which keeps `pio run` usable on its own.
 */
#pragma once

#include <cstddef>
#include <cstdint>

#if __has_include("bench_config.h")
#include "bench_config.h"
#endif

#ifndef BENCH_TECH
#define BENCH_TECH None        // None | EspNow | LoRa | Zigbee
#define BENCH_RUN_ID 0         // tells the gateway a new run began, so it restarts its count
#define BENCH_PAYLOAD 32
#define BENCH_EXCHANGES 0
#define BENCH_IDLE_MS 0        // hold with the radio up but silent: the standing-current window
#define BENCH_REPLY_WAIT_MS 2000
#define BENCH_LEASE_S 60       // how long the gateway stays on a LoRa point before going home
#define BENCH_LORA_BW_KHZ 125
#define BENCH_LORA_SF 9
#define BENCH_LORA_CR 7
#define BENCH_LORA_PWR 7
#define BENCH_LORA_PREAMBLE 8
#define BENCH_ESPNOW_CHANNEL 1
#define BENCH_PEER_MAC {0, 0, 0, 0, 0, 0}
#define BENCH_PEER_XBEE 0x0ULL
#endif

#define BENCH_STRINGIFY_(x) #x
#define BENCH_STRINGIFY(x) BENCH_STRINGIFY_(x)

namespace bench {

enum class Tech : uint8_t { None, EspNow, LoRa, Zigbee };

struct LoRaParams {
    uint8_t bandwidthKHz;
    uint8_t spreadingFactor;
    uint8_t codingRate;   // the N of 4/N
    int8_t txPowerDbm;
    uint8_t preambleLength;
};

constexpr Tech kTech = Tech::BENCH_TECH;
constexpr const char *kTechName = BENCH_STRINGIFY(BENCH_TECH);
constexpr uint8_t kRunId = BENCH_RUN_ID;
constexpr size_t kPayloadBytes = BENCH_PAYLOAD;
constexpr uint32_t kExchanges = BENCH_EXCHANGES;
constexpr uint32_t kIdleMs = BENCH_IDLE_MS;
constexpr uint32_t kReplyWaitMs = BENCH_REPLY_WAIT_MS;
constexpr uint32_t kLeaseSeconds = BENCH_LEASE_S;
constexpr uint32_t kSpacingMs = 100;   // pause before every ping, so exchanges never overlap

constexpr float kFrequencyMHz = 433.2f;
constexpr LoRaParams kPoint{BENCH_LORA_BW_KHZ, BENCH_LORA_SF, BENCH_LORA_CR, BENCH_LORA_PWR,
                            BENCH_LORA_PREAMBLE};
// Home: the slowest, most robust LoRa setting. The gateway waits here, and every LoRa point is
// announced here, because a receiver hears only one SF/BW at a time. If home does not reach, no
// swept point would have either.
constexpr LoRaParams kHome{125, 12, 8, 7, 8};

constexpr uint8_t kEspNowChannel = BENCH_ESPNOW_CHANNEL;
constexpr uint8_t kPeerMac[6] = BENCH_PEER_MAC;      // the other board's WiFi MAC
constexpr uint64_t kPeerXbee64 = BENCH_PEER_XBEE;    // the gateway's XBee, used by the node

// The EU envelope (docs/regulations.md), checked by the compiler: a configuration outside it does
// not build, so it can never be flashed, let alone recorded as measured.
static_assert(kPayloadBytes >= 8, "payload must hold the 8-byte ping/pong header");
static_assert(kPayloadBytes <= (kTech == Tech::Zigbee ? 84 : kTech == Tech::LoRa ? 249 : 250),
              "payload exceeds this radio's single-packet limit");
static_assert(kPoint.txPowerDbm <= 7, "above 7 dBm conducted exceeds 10 mW e.r.p.");
static_assert(kPoint.spreadingFactor >= 7 && kPoint.spreadingFactor <= 12, "SF must be 7..12");
static_assert(kPoint.codingRate >= 5 && kPoint.codingRate <= 8, "coding rate must be 4/5..4/8");
static_assert(kPoint.bandwidthKHz == 125 || kPoint.bandwidthKHz == 250,
              "only 125 and 250 kHz fit inside 433.050-434.790 MHz around 433.2 MHz");
static_assert(kEspNowChannel >= 1 && kEspNowChannel <= 13, "WiFi channel must be 1..13 (EU)");
static_assert(kLeaseSeconds <= 1020, "the lease travels as one byte of 4 s steps");

}   // namespace bench
