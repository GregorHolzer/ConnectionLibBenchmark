#include "raw/RawTransports.hpp"

#if defined(ESP32)

#include <algorithm>

#include "raw/RawEspNowTransport.hpp"
#include "raw/RawLoRaTransport.hpp"
#include "raw/RawZigbeeTransport.hpp"

namespace bench {

std::unique_ptr<connectionlib::Transport> createRawTransport(const BenchConfig &config) {
    switch (config.tech) {
        case Tech::EspNow: {
            RawEspNowParams params;
            std::copy(config.peerMac, config.peerMac + 6, params.peerMac.begin());
            params.wifiChannel = config.wifiChannel;
            params.encrypt = config.encrypt;
            params.sendTimeoutMs = config.sendTimeoutMs;
            params.captureRssi = config.captureRssi;
            return std::make_unique<RawEspNowTransport>(params);
        }
        case Tech::LoRa: {
            RawLoRaParams params;
            params.frequencyMHz = config.frequencyMHz;
            params.bandwidthKHz = config.bandwidthKHz;
            params.spreadingFactor = config.spreadingFactor;
            params.codingRate = config.codingRate;
            params.txPowerDbm = config.txPowerDbm;
            params.preambleLength = config.preambleLength;
            return std::make_unique<RawLoRaTransport>(params);
        }
        case Tech::Zigbee: {
            RawZigbeeParams params;
            params.peerXbee64 = config.peerXbee64;
            params.broadcast = config.broadcast;
            params.captureRssi = config.captureRssi;
            return std::make_unique<RawZigbeeTransport>(params);
        }
        case Tech::None:
            return nullptr;
    }
    return nullptr;
}

}   // namespace bench

#endif   // defined(ESP32)
