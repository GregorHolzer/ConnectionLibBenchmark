#include "raw/RawLoRaTransport.hpp"

#if defined(ESP32)

#include <algorithm>

#include <Arduino.h>

namespace bench {

using connectionlib::Address;
using connectionlib::ReceivedMessage;
using connectionlib::SendResult;
using connectionlib::SendStatus;

RawLoRaTransport::RawLoRaTransport(RawLoRaParams params)
    : params_(params),
      // The same EU envelope ConnectionLib's LoRaConfig defaults to: 10 % over a rolling hour.
      // Held here rather than dropped, because the regulation binds the radio, not the library.
      dutyCycle_(connectionlib::DutyCycleConfig{10.0f, 3'600'000}),
      spi_(HSPI),
      radio_(new Module(params_.nss, params_.dio0, params_.reset, RADIOLIB_NC, spi_)) {}

bool RawLoRaTransport::setup() {
    spi_.begin(params_.sck, params_.miso, params_.mosi, params_.nss);

    int16_t state = radio_.begin(params_.frequencyMHz, params_.bandwidthKHz, params_.spreadingFactor,
                                  params_.codingRate, RADIOLIB_SX127X_SYNC_WORD, params_.txPowerDbm,
                                  params_.preambleLength);
    lastError_ = state;
    if (state != RADIOLIB_ERR_NONE) {
        return false;
    }

    state = radio_.startReceive();
    lastError_ = state;
    return state == RADIOLIB_ERR_NONE;
}

int RawLoRaTransport::lastErrorCode() const {
    return lastError_;
}

SendResult RawLoRaTransport::send(const Address &destination, const uint8_t *payload, size_t len) {
    // The destination is ignored, and that is the point: a raw SX1278 has no addressing whatever,
    // which is precisely why ConnectionLib has to add six bytes of its own. Every listening radio
    // on the frequency hears this packet; with the two boards of this bench that is exactly the
    // set of radios the exchange is meant for.
    (void)destination;

    SendResult result;
    result.payloadBytes = len;
    result.acknowledged = false;   // no MAC-layer ack exists at this layer, same as the library arm

    if (len > maxPayloadBytes()) {
        result.status = SendStatus::PayloadTooLarge;
        return result;
    }

    uint32_t estimatedMs = static_cast<uint32_t>(radio_.getTimeOnAir(len) / 1000);
    if (!dutyCycle_.canTransmit(millis(), estimatedMs)) {
        result.status = SendStatus::DutyCycleExceeded;
        return result;
    }

    uint32_t t0 = micros();
    int16_t state = radio_.transmit(const_cast<uint8_t *>(payload), len);
    uint32_t t1 = micros();

    radio_.startReceive();   // transmit() leaves receive mode, as in the library arm

    result.timestampMicros = t1;
    // No framing, so bytes-on-air equals the payload. This is the number that differs from the
    // library arm, and the one the airtime and energy differences follow from.
    result.totalBytes = len;
    dutyCycle_.recordTransmission(millis(), (t1 - t0) / 1000);

    lastError_ = state;
    result.status = (state == RADIOLIB_ERR_NONE) ? SendStatus::Ok : SendStatus::Failed;
    return result;
}

std::optional<ReceivedMessage> RawLoRaTransport::poll() {
    if (digitalRead(params_.dio0) == LOW) {
        return std::nullopt;
    }

    size_t receivedLen = radio_.getPacketLength();
    if (receivedLen == 0 || receivedLen > kMaxRadioPacket) {
        radio_.startReceive();
        return std::nullopt;
    }

    uint8_t buffer[kMaxRadioPacket];
    int16_t state = radio_.readData(buffer, receivedLen);
    // Read before re-arming: startReceive() clears the registers these come from.
    float rssi = radio_.getRSSI();
    float snr = radio_.getSNR();
    radio_.startReceive();

    if (state != RADIOLIB_ERR_NONE) {
        lastError_ = state;
        return std::nullopt;
    }

    ReceivedMessage msg;
    // Without a header there is no source to report. Broadcast is the honest answer - the packet
    // genuinely carries no sender identity - and the bench matches replies on its own payload
    // rather than on the address, so nothing downstream depends on this.
    msg.source = broadcastAddress();
    msg.payload.assign(buffer, buffer + receivedLen);
    msg.timestampMicros = micros();
    msg.totalBytes = receivedLen;
    // Free on this radio: RadioLib reads both out of the chip for a packet that already arrived,
    // so unlike ESP-NOW and Zigbee there is nothing to gate behind a capture flag.
    msg.rssiDbm = rssi;
    msg.snrDb = snr;
    return msg;
}

Address RawLoRaTransport::broadcastAddress() const {
    return Address::loRaNode(0xFFFF);
}

size_t RawLoRaTransport::maxPayloadBytes() const {
    return kMaxRadioPacket;   // the full packet: no header is subtracted, because there is none
}

const char *RawLoRaTransport::technologyName() const {
    return "LoRa-SX1278-raw";
}

float RawLoRaTransport::currentDutyCyclePercent() const {
    return dutyCycle_.currentDutyCyclePercent(millis());
}

uint32_t RawLoRaTransport::timeUntilNextAllowedMs(size_t payloadLen) const {
    uint32_t estimatedMs = static_cast<uint32_t>(radio_.getTimeOnAir(payloadLen) / 1000);
    return dutyCycle_.timeUntilNextAllowedMs(millis(), estimatedMs);
}

}   // namespace bench

#endif   // defined(ESP32)
