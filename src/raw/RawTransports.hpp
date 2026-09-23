#pragma once

#include <array>
#include <cstdint>
#include <memory>

#include <ConnectionLib/Transport.hpp>

#include "BenchConfig.hpp"

/**
 * The no-library arm of the benchmark.
 *
 * The exposé asks for results reported "so that what the library contributes stays separable from
 * what the radios contribute". Nothing measured that, so these three transports do: each talks to
 * the same vendor API the corresponding ConnectionLib backend wraps — RadioLib's SX1278,
 * esp_now.h, xbee-arduino's ZBTxRequest — with the library's own logic out of the path.
 *
 * **Why they still implement connectionlib::Transport.** The alternative, a second measurement
 * loop, would make the two arms incomparable: any difference could then be the loop rather than
 * the library. Sharing the interface means runGatewayPoint() measures both with literally the same
 * code, the same timing points and the same emitted records, so the only thing that varies is what
 * happens inside send() and poll(). One virtual call per exchange survives in both arms, cancels,
 * and sits far below the harness floor runNullMeasurement() already quantifies (practice P4).
 *
 * **What is actually bypassed**, and the claim these numbers support:
 *
 * | | bypassed | kept |
 * |---|---|---|
 * | LoRa    | the 6-byte FrameHeader, Address translation | duty-cycle enforcement |
 * | ESP-NOW | Address translation, the receive ring buffer | — |
 * | Zigbee  | Address translation, the send-window RX buffering | — |
 *
 * Duty-cycle enforcement is regulatory, not a library feature under test, so it stays in the raw
 * LoRa arm and the delta must be read as "framing, addressing and queueing" rather than as the
 * library's whole cost.
 *
 * Expect LoRa to show a real, payload-dependent penalty and the other two to come out within
 * noise. "The wrapper is free on two of three radios" is worth having as a measurement.
 */
namespace bench {

/** @brief The subset of BenchConfig the raw LoRa arm needs, so it never sees the library's config. */
struct RawLoRaParams {
    float frequencyMHz = 433.2f;
    float bandwidthKHz = 125.0f;
    uint8_t spreadingFactor = 9;
    uint8_t codingRate = 7;
    int8_t txPowerDbm = 7;
    uint16_t preambleLength = 8;
    // Pin map of the T-Beam's SX1278, mirroring ConnectionLib's LoRaPinConfig defaults.
    uint8_t sck = 5, miso = 19, mosi = 27, nss = 18, reset = 23, dio0 = 26;
};

struct RawEspNowParams {
    std::array<uint8_t, 6> peerMac{};
    uint8_t wifiChannel = 1;
    bool encrypt = false;
    uint32_t sendTimeoutMs = 2000;
    bool captureRssi = false;
};

struct RawZigbeeParams {
    uint64_t peerXbee64 = 0;
    bool broadcast = false;
    uint32_t baud = 9600;
    uint8_t rxPin = 13, txPin = 25;
    bool captureRssi = false;
};

/** @brief Build the raw transport for `config`, or nullptr if the technology has none. */
std::unique_ptr<connectionlib::Transport> createRawTransport(const BenchConfig &config);

}   // namespace bench
