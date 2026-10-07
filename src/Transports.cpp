#include "Transports.hpp"

#include <algorithm>
#include <array>

#include <ConnectionLib/backends/espnow/EspNowTransport.hpp>
#include <ConnectionLib/backends/lora/LoRaTransport.hpp>
#include <ConnectionLib/backends/zigbee/ZigbeeTransport.hpp>

using namespace connectionlib;

namespace bench {

namespace {

// LoRa has no hardware address, so ConnectionLib gives each board a software id.
constexpr uint16_t kGatewayLoRaId = 1;
constexpr uint16_t kNodeLoRaId = 2;

Address peerMac() {
    std::array<uint8_t, 6> mac{};
    std::copy(kPeerMac, kPeerMac + 6, mac.begin());
    return Address::mac(mac);
}

}   // namespace

std::unique_ptr<Transport> makeLoRa(const LoRaParams &params) {
    LoRaConfig cfg;   // duty cycle stays at the library's EU default of 10 % per hour
    cfg.frequencyMHz = kFrequencyMHz;
    cfg.bandwidthKHz = params.bandwidthKHz;
    cfg.spreadingFactor = params.spreadingFactor;
    cfg.codingRate = params.codingRate;
    cfg.txPowerDbm = params.txPowerDbm;
    cfg.preambleLength = params.preambleLength;
#if defined(BENCH_GATEWAY)
    cfg.nodeId = kGatewayLoRaId;
#else
    cfg.nodeId = kNodeLoRaId;
#endif
    return std::make_unique<LoRaTransport>(cfg);
}

std::unique_ptr<Transport> makeEspNow() {
    EspNowConfig cfg;
    cfg.peers.push_back(peerMac());   // ESP-NOW only sends to registered peers
    cfg.wifiChannel = kEspNowChannel;
    return std::make_unique<EspNowTransport>(cfg);
}

std::unique_ptr<Transport> makeZigbee() {
    return std::make_unique<ZigbeeTransport>(ZigbeeConfig{});
}

std::unique_ptr<Transport> makeTransport(Tech tech) {
    switch (tech) {
        case Tech::EspNow: return makeEspNow();
        case Tech::LoRa: return makeLoRa(kPoint);
        case Tech::Zigbee: return makeZigbee();
        case Tech::None: break;
    }
    return nullptr;
}

Address gatewayAddress(Tech tech) {
    switch (tech) {
        case Tech::EspNow: return peerMac();
        case Tech::LoRa: return Address::loRaNode(kGatewayLoRaId);
        case Tech::Zigbee: return Address::xbee(kPeerXbee64);   // unicast: a broadcast blocks ~1 s
        case Tech::None: break;
    }
    return Address();
}

}   // namespace bench
