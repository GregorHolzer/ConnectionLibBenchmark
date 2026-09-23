/**
 * @brief Zigbee straight onto xbee-arduino, bypassing ConnectionLib.
 *
 * Builds and parses XBee API frames itself. Keeps a single-slot pendingRx_ for the same reason the
 * library backend does: a reply can land inside the TX-status wait, and dropping it would count as
 * a loss something that actually arrived.
 */

#include "raw/RawZigbeeTransport.hpp"

#if defined(ESP32)

#include <Arduino.h>

namespace bench {

using connectionlib::Address;
using connectionlib::AddressKind;
using connectionlib::ReceivedMessage;
using connectionlib::SendResult;
using connectionlib::SendStatus;

RawZigbeeTransport::RawZigbeeTransport(RawZigbeeParams params) : params_(params) {}

bool RawZigbeeTransport::setup() {
    serial_.begin(params_.baud, SERIAL_8N1, params_.rxPin, params_.txPin);
    xbee_.begin(serial_);
    return true;
}

int RawZigbeeTransport::lastErrorCode() const {
    return lastError_;
}

SendResult RawZigbeeTransport::send(const Address &destination, const uint8_t *payload, size_t len) {
    (void)destination;   // the configured 64-bit address is used directly, untranslated

    SendResult result;
    result.payloadBytes = len;
    if (len > kMaxPayload) {
        result.status = SendStatus::PayloadTooLarge;
        return result;
    }

    XBeeAddress64 addr64 = params_.broadcast
                               ? XBeeAddress64(0x00000000, 0x0000FFFF)
                               : XBeeAddress64(static_cast<uint32_t>(params_.peerXbee64 >> 32),
                                                static_cast<uint32_t>(params_.peerXbee64));
    ZBTxRequest request(addr64, const_cast<uint8_t *>(payload), static_cast<uint8_t>(len));

    uint32_t t0 = micros();
    xbee_.send(request);

    bool acknowledged = false;
    bool statusSeen = false;
    uint32_t waitStart = millis();
    while (millis() - waitStart < kTxStatusWaitMs) {
        int remaining = static_cast<int>(kTxStatusWaitMs - (millis() - waitStart));
        if (remaining <= 0 || !xbee_.readPacket(remaining)) {
            break;
        }
        uint8_t apiId = xbee_.getResponse().getApiId();
        if (apiId == ZB_TX_STATUS_RESPONSE) {
            ZBTxStatusResponse txStatus;
            xbee_.getResponse().getZBTxStatusResponse(txStatus);
            acknowledged = txStatus.isSuccess();
            statusSeen = true;
            break;
        }
        if (apiId == ZB_RX_RESPONSE && !pendingRx_) {
            // The peer's reply routinely lands inside this window - a broadcast status takes
            // about a second to resolve - so hold it rather than dropping it.
            pendingRx_ = buildReceived();
        }
    }
    uint32_t t1 = micros();

    result.timestampMicros = t1;
    result.totalBytes = len;   // addressed natively; the library adds no bytes here either
    result.acknowledged = acknowledged;
    result.status = statusSeen ? (acknowledged ? SendStatus::Ok : SendStatus::Failed)
                               : SendStatus::Timeout;
    return result;
}

std::optional<ReceivedMessage> RawZigbeeTransport::poll() {
    if (pendingRx_) {
        ReceivedMessage msg = std::move(*pendingRx_);
        pendingRx_.reset();
        return msg;
    }

    xbee_.readPacket();
    if (!xbee_.getResponse().isAvailable()) {
        return std::nullopt;
    }
    if (xbee_.getResponse().getApiId() != ZB_RX_RESPONSE) {
        return std::nullopt;
    }
    return buildReceived();
}

ReceivedMessage RawZigbeeTransport::buildReceived() {
    ZBRxResponse rx;
    xbee_.getResponse().getZBRxResponse(rx);

    ReceivedMessage msg;
    msg.source = Address::xbee(rx.getRemoteAddress64().get(), rx.getRemoteAddress16());
    msg.payload.assign(rx.getData(), rx.getData() + rx.getDataLength());
    msg.timestampMicros = micros();
    msg.totalBytes = rx.getDataLength();
    if (params_.captureRssi) {
        msg.rssiDbm = queryLastRssi();
    }
    return msg;
}

std::optional<float> RawZigbeeTransport::queryLastRssi() {
    // `DB` reports the magnitude of the last received packet's RSSI in dBm; the sign is implied.
    uint8_t command[] = {'D', 'B'};
    AtCommandRequest request(command);
    xbee_.send(request);

    uint32_t waitStart = millis();
    while (millis() - waitStart < kRssiQueryWaitMs) {
        int remaining = static_cast<int>(kRssiQueryWaitMs - (millis() - waitStart));
        if (remaining <= 0 || !xbee_.readPacket(remaining)) {
            break;
        }
        if (xbee_.getResponse().getApiId() != AT_COMMAND_RESPONSE) {
            continue;
        }
        AtCommandResponse response;
        xbee_.getResponse().getAtCommandResponse(response);
        if (response.isOk() && response.getValueLength() >= 1) {
            return -static_cast<float>(response.getValue()[0]);
        }
        break;
    }
    return std::nullopt;
}

Address RawZigbeeTransport::broadcastAddress() const {
    return Address::broadcast(AddressKind::XBee64_16);
}

size_t RawZigbeeTransport::maxPayloadBytes() const {
    return kMaxPayload;
}

const char *RawZigbeeTransport::technologyName() const {
    return "Zigbee-XBee-raw";
}

float RawZigbeeTransport::currentDutyCyclePercent() const {
    return 0.0f;   // no duty-cycle limit in 2.4 GHz for adaptive equipment; see the ESP-NOW arm
}

uint32_t RawZigbeeTransport::timeUntilNextAllowedMs(size_t payloadLen) const {
    (void)payloadLen;
    return 0;
}

}   // namespace bench

#endif   // defined(ESP32)
