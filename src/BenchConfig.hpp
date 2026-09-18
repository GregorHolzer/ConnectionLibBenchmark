#pragma once

#include <cstddef>
#include <cstdint>

// One configuration point, as parsed from a `CFG` command. Deliberately a flat POD holding every
// technology's parameters rather than a variant: it is populated from a key=value line, most keys
// are absent in any given command, and defaults-plus-overwrite is the simplest thing that works.
namespace bench {

enum class Tech : uint8_t {
    None,   // null measurement: the harness with no radio, for the overhead figure (practice P4)
    EspNow,
    LoRa,
    Zigbee,
};

struct BenchConfig {
    Tech tech = Tech::None;

    // Shared across technologies
    uint16_t payloadBytes = 32;
    uint32_t exchanges = 20;
    uint32_t spacingMs = 100;
    uint32_t replyWaitMs = 4000;

    // LoRa. Defaults mirror ConnectionLib's LoRaConfig, which is the EU-legal operating point -
    // see docs/regulations.md. The sweep moves within that envelope, never past it.
    float frequencyMHz = 433.2f;
    float bandwidthKHz = 125.0f;
    uint8_t spreadingFactor = 9;
    uint8_t codingRate = 7;
    int8_t txPowerDbm = 7;
    uint16_t preambleLength = 8;

    // ESP-NOW
    uint8_t wifiChannel = 1;
    bool encrypt = false;
    uint32_t sendTimeoutMs = 2000;

    // Zigbee
    bool broadcast = false;   // false = unicast to peerXbee64

    // Peer addressing, supplied per-configuration rather than baked in at build time. This is what
    // lets one flashed image serve an entire campaign: the host reads each board's MAC once and
    // passes it in every CFG, so changing the peer never requires a rebuild.
    uint8_t peerMac[6] = {0, 0, 0, 0, 0, 0};
    uint64_t peerXbee64 = 0;
};

}   // namespace bench
