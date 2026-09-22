/**
 * @brief The EU radio envelope, checked before a configuration is allowed to run.
 *
 * Rejecting here rather than clamping is deliberate: a silently corrected parameter would be
 * recorded as the value the host asked for, and the results would claim a configuration that was
 * never measured. See docs/regulations.md.
 */

#include "Conformance.hpp"

namespace bench {

namespace {
// CEPT ERC-REC 70-03 Annex 1 band h1.3 / ETSI EN 300 220-2. See docs/regulations.md.
constexpr float kBandLowMHz = 433.050f;
constexpr float kBandHighMHz = 434.790f;

// 10mW e.r.p. is the ceiling. 7dBm conducted leaves ~9dBm e.r.p. through a ~2dBi antenna; the
// SX1278's 17dBm default would be 50mW, five times over.
constexpr int8_t kMaxTxPowerDbm = 7;

// XBee S2C ZB firmware's unfragmented RF payload limit; ESP-NOW's is 250 and LoRa's 249.
constexpr uint16_t kZigbeeMaxPayload = 84;
constexpr uint16_t kEspNowMaxPayload = 250;
constexpr uint16_t kLoRaMaxPayload = 249;
}   // namespace

const char *checkConformance(const BenchConfig &config) {
    if (config.exchanges == 0) {
        return "exchanges must be > 0";
    }
    if (config.payloadBytes < 3) {
        return "payload must be >= 3 bytes (kind + 16-bit sequence number)";
    }

    switch (config.tech) {
        case Tech::LoRa: {
            // The occupied bandwidth must fit inside the band, not just the centre frequency.
            // This is what rules out BW500 at a 433.2MHz centre: 433.2 +/- 0.25 leaves the band
            // at the bottom. It is also the bug that shipped in LoRaConfig - a 433.0 centre put a
            // 125kHz signal below the 433.050 edge.
            float halfBandwidthMHz = config.bandwidthKHz / 2000.0f;
            if (config.frequencyMHz - halfBandwidthMHz < kBandLowMHz) {
                return "occupied bandwidth falls below the 433.050MHz band edge";
            }
            if (config.frequencyMHz + halfBandwidthMHz > kBandHighMHz) {
                return "occupied bandwidth exceeds the 434.790MHz band edge";
            }
            if (config.txPowerDbm > kMaxTxPowerDbm) {
                return "txPower exceeds the 10mW e.r.p. ceiling (max 7dBm conducted)";
            }
            if (config.spreadingFactor < 6 || config.spreadingFactor > 12) {
                return "spreadingFactor must be 6..12";
            }
            if (config.codingRate < 5 || config.codingRate > 8) {
                return "codingRate must be 5..8 (as the denominator of 4/N)";
            }
            if (config.payloadBytes > kLoRaMaxPayload) {
                return "payload exceeds the LoRa maximum of 249 bytes";
            }
            return nullptr;
        }
        case Tech::EspNow:
            if (config.wifiChannel < 1 || config.wifiChannel > 13) {
                return "wifiChannel must be 1..13 (EU)";
            }
            if (config.payloadBytes > kEspNowMaxPayload) {
                return "payload exceeds the ESP-NOW maximum of 250 bytes";
            }
            return nullptr;

        case Tech::Zigbee:
            if (config.payloadBytes > kZigbeeMaxPayload) {
                return "payload exceeds the Zigbee maximum of 84 bytes";
            }
            return nullptr;

        case Tech::None:
            return nullptr;
    }
    return "unknown technology";
}

}   // namespace bench
