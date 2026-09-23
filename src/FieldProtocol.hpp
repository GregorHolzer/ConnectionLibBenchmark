/**
 * @brief Field mode's over-the-air control frames, shared by the field node and field gateway.
 *
 * In the field the gateway has no host, so the configuration for each point reaches it over the
 * radio instead of a serial CFG. That can't happen over the configuration being swept - a point
 * that fails would strand the gateway somewhere the node can no longer reach - so control only
 * ever travels over one fixed, most-robust LoRa configuration, the *home* config, and every lease
 * ends back on home whether the point worked or not. See docs/field-campaign.md.
 *
 * Frames ride inside an ordinary Transport payload, next to the ping/pong kinds 'P'/'Q':
 *
 *   'C' lease request   node -> gateway, on home: campaign id, point id, lease length, and the
 *                       radio half of a BenchConfig
 *   'A' lease ack       gateway -> node, on home
 *   'R' release         node -> gateway, on the point's config, once the point has finished
 *   'K' release ack     gateway -> node, on the point's config
 *
 * Binary rather than the CFG text line because every byte on home costs SF12 airtime against the
 * 10 % duty cycle. Only what the *responder* needs is carried: it echoes pings at whatever size
 * they arrive and replies to their source, so payload size, exchange count and peer XBee address
 * stay on the node.
 */

#pragma once

#include <cstddef>
#include <cstdint>

#include "BenchConfig.hpp"

namespace bench::field {

constexpr uint8_t kLeaseKind = 'C';
constexpr uint8_t kLeaseAckKind = 'A';
constexpr uint8_t kReleaseKind = 'R';
constexpr uint8_t kReleaseAckKind = 'K';

constexpr uint8_t kProtocolVersion = 1;

constexpr size_t kLeaseFrameBytes = 35;
constexpr size_t kTokenFrameBytes = 8;

/** @brief Lease ack status: the gateway switched, or it refused the configuration. */
constexpr uint8_t kStatusOk = 0;
constexpr uint8_t kStatusRejected = 1;

/**
 * @brief The home configuration: SF12, 125 kHz, CR 4/8, 7 dBm - the slowest, most robust point in
 * the docs/regulations.md envelope.
 *
 * If home fails at some distance, none of the swept points would have worked either. bench/field.py
 * mirrors these values to charge control airtime against the duty cycle; keep the two in step.
 */
BenchConfig homeConfig();

struct LeaseRequest {
    uint32_t campaignId = 0;
    uint16_t pointId = 0;
    uint16_t leaseSeconds = 0;
    BenchConfig config;   // the gateway's config for the point; peerMac is the node's MAC
};

/** @brief Lease ack, release and release ack: a kind plus the lease they refer to. */
struct Token {
    uint8_t kind = 0;
    uint32_t campaignId = 0;
    uint16_t pointId = 0;
    uint8_t status = kStatusOk;
};

/** @brief Writes kLeaseFrameBytes into `out`. */
void encodeLease(const LeaseRequest &request, uint8_t *out);
bool decodeLease(const uint8_t *data, size_t len, LeaseRequest &out);

/** @brief Writes kTokenFrameBytes into `out`. */
void encodeToken(const Token &token, uint8_t *out);
bool decodeToken(const uint8_t *data, size_t len, uint8_t expectedKind, Token &out);

}   // namespace bench::field
