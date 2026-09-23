/** @brief Constructing the configured transport and its peer address. See TransportFactory.hpp. */

#include "TransportFactory.hpp"

#include <algorithm>
#include <array>

#include <ConnectionLib/backends/espnow/EspNowTransport.hpp>
#include <ConnectionLib/backends/lora/LoRaTransport.hpp>
#include <ConnectionLib/backends/zigbee/ZigbeeTransport.hpp>

#include "raw/RawTransports.hpp"

using namespace connectionlib;

namespace bench {

std::unique_ptr<Transport> buildTransport(const BenchConfig &config) {
    // The raw arm short-circuits before any ConnectionLib backend is constructed. Everything
    // downstream - runGatewayPoint, the timing points, the emitted records - is unchanged, which
    // is what makes the library-versus-raw difference attributable to the library itself.
    if (config.stack == Stack::Raw) {
        return createRawTransport(config);
    }

    switch (config.tech) {
        case Tech::EspNow: {
            EspNowConfig cfg;
            std::array<uint8_t, 6> mac{};
            std::copy(config.peerMac, config.peerMac + 6, mac.begin());
            cfg.peers.push_back(Address::mac(mac));
            cfg.wifiChannel = config.wifiChannel;
            cfg.encrypt = config.encrypt;
            cfg.sendTimeoutMs = config.sendTimeoutMs;
            cfg.captureRssi = config.captureRssi;
            return std::make_unique<EspNowTransport>(cfg);
        }
        case Tech::LoRa: {
            LoRaConfig cfg;   // duty cycle left at ConnectionLib's EU-legal default, never relaxed
            cfg.frequencyMHz = config.frequencyMHz;
            cfg.bandwidthKHz = config.bandwidthKHz;
            cfg.spreadingFactor = config.spreadingFactor;
            cfg.codingRate = config.codingRate;
            cfg.txPowerDbm = config.txPowerDbm;
            cfg.preambleLength = config.preambleLength;
#if defined(BENCH_ROLE_GATEWAY)
            cfg.nodeId = 1;
#else
            cfg.nodeId = 2;
#endif
            return std::make_unique<LoRaTransport>(cfg);
        }
        case Tech::Zigbee: {
            ZigbeeConfig cfg;
            cfg.captureRssi = config.captureRssi;
            return std::make_unique<ZigbeeTransport>(cfg);
        }
        case Tech::None:
            return nullptr;
    }
    return nullptr;
}

Address peerAddress(const BenchConfig &config) {
    switch (config.tech) {
        case Tech::EspNow: {
            std::array<uint8_t, 6> mac{};
            std::copy(config.peerMac, config.peerMac + 6, mac.begin());
            return Address::mac(mac);
        }
        case Tech::LoRa:
#if defined(BENCH_ROLE_GATEWAY)
            return Address::loRaNode(2);
#else
            return Address::loRaNode(1);
#endif
        case Tech::Zigbee:
            // Unicast by default. A ZB broadcast blocks ~1s inside send() waiting for its APS
            // status and the reply lands inside that window, so it is buffered and the round trip
            // measures 0ms. Broadcast stays available as a swept parameter precisely so that
            // difference can be quantified rather than assumed.
            if (config.broadcast || config.peerXbee64 == 0) {
                return Address::broadcast(AddressKind::XBee64_16);
            }
            return Address::xbee(config.peerXbee64);
        case Tech::None:
            break;
    }
    return Address::broadcast(AddressKind::Broadcast);
}

}   // namespace bench
