/** @brief Encoding the field-mode control frames. See FieldProtocol.hpp. */

#include "FieldProtocol.hpp"

#include <cstring>

namespace bench::field {

namespace {

// Explicit little-endian, field by field: a packed struct would tie the wire format to the
// compiler's layout, and both ends are ESP32s today but the log parser on the host is not.
void put16(uint8_t *out, uint16_t value) {
    out[0] = static_cast<uint8_t>(value);
    out[1] = static_cast<uint8_t>(value >> 8);
}

void put32(uint8_t *out, uint32_t value) {
    for (int i = 0; i < 4; i++) {
        out[i] = static_cast<uint8_t>(value >> (8 * i));
    }
}

uint16_t get16(const uint8_t *in) {
    return static_cast<uint16_t>(in[0] | (in[1] << 8));
}

uint32_t get32(const uint8_t *in) {
    uint32_t value = 0;
    for (int i = 0; i < 4; i++) {
        value |= static_cast<uint32_t>(in[i]) << (8 * i);
    }
    return value;
}

void putFloat(uint8_t *out, float value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    put32(out, bits);
}

float getFloat(const uint8_t *in) {
    uint32_t bits = get32(in);
    float value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

}   // namespace

BenchConfig homeConfig() {
    BenchConfig config;
    config.tech = Tech::LoRa;
    config.frequencyMHz = 433.2f;
    config.bandwidthKHz = 125.0f;
    config.spreadingFactor = 12;
    config.codingRate = 8;
    config.txPowerDbm = 7;
    config.preambleLength = 8;
    return config;
}

// Layout (35 bytes):
//   0 kind 'C'   1 version   2-5 campaign id   6-7 point id   8-9 lease seconds
//   10 tech   11 stack   12 flags (bit0 rssi, bit1 encrypt)   13-16 frequency MHz (float)
//   17-20 bandwidth kHz (float)   21 SF   22 CR   23 power dBm (int8)   24-25 preamble
//   26 WiFi channel   27-28 ESP-NOW send timeout ms   29-34 node MAC
void encodeLease(const LeaseRequest &request, uint8_t *out) {
    const BenchConfig &c = request.config;
    out[0] = kLeaseKind;
    out[1] = kProtocolVersion;
    put32(out + 2, request.campaignId);
    put16(out + 6, request.pointId);
    put16(out + 8, request.leaseSeconds);
    out[10] = static_cast<uint8_t>(c.tech);
    out[11] = static_cast<uint8_t>(c.stack);
    out[12] = static_cast<uint8_t>((c.captureRssi ? 1 : 0) | (c.encrypt ? 2 : 0));
    putFloat(out + 13, c.frequencyMHz);
    putFloat(out + 17, c.bandwidthKHz);
    out[21] = c.spreadingFactor;
    out[22] = c.codingRate;
    out[23] = static_cast<uint8_t>(c.txPowerDbm);
    put16(out + 24, c.preambleLength);
    out[26] = c.wifiChannel;
    put16(out + 27, static_cast<uint16_t>(c.sendTimeoutMs > 0xFFFF ? 0xFFFF : c.sendTimeoutMs));
    std::memcpy(out + 29, c.peerMac, 6);
}

bool decodeLease(const uint8_t *data, size_t len, LeaseRequest &out) {
    if (len != kLeaseFrameBytes || data[0] != kLeaseKind || data[1] != kProtocolVersion) {
        return false;
    }
    if (data[10] > static_cast<uint8_t>(Tech::Zigbee) || data[11] > static_cast<uint8_t>(Stack::Raw)) {
        return false;
    }
    out = LeaseRequest{};
    BenchConfig &c = out.config;
    out.campaignId = get32(data + 2);
    out.pointId = get16(data + 6);
    out.leaseSeconds = get16(data + 8);
    c.tech = static_cast<Tech>(data[10]);
    c.stack = static_cast<Stack>(data[11]);
    c.captureRssi = (data[12] & 1) != 0;
    c.encrypt = (data[12] & 2) != 0;
    c.frequencyMHz = getFloat(data + 13);
    c.bandwidthKHz = getFloat(data + 17);
    c.spreadingFactor = data[21];
    c.codingRate = data[22];
    c.txPowerDbm = static_cast<int8_t>(data[23]);
    c.preambleLength = get16(data + 24);
    c.wifiChannel = data[26];
    c.sendTimeoutMs = get16(data + 27);
    std::memcpy(c.peerMac, data + 29, 6);
    return true;
}

// Layout (8 bytes): 0 kind   1-4 campaign id   5-6 point id   7 status
void encodeToken(const Token &token, uint8_t *out) {
    out[0] = token.kind;
    put32(out + 1, token.campaignId);
    put16(out + 5, token.pointId);
    out[7] = token.status;
}

bool decodeToken(const uint8_t *data, size_t len, uint8_t expectedKind, Token &out) {
    if (len != kTokenFrameBytes || data[0] != expectedKind) {
        return false;
    }
    out.kind = data[0];
    out.campaignId = get32(data + 1);
    out.pointId = get16(data + 5);
    out.status = data[7];
    return true;
}

}   // namespace bench::field
