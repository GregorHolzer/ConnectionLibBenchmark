#pragma once

#include <ConnectionLib/Transport.hpp>

#if defined(ESP32)

#include <HardwareSerial.h>
#include <XBee.h>

#include "raw/RawTransports.hpp"

namespace bench {

/**
 * @brief Zigbee with xbee-arduino driven directly and no ConnectionLib in the path.
 *
 * Like the ESP-NOW arm, expected to land within noise of ZigbeeTransport: the module does the
 * network layer itself, so the library's backend only frames ZB TX/RX requests and buffers a
 * receive that lands inside the send window. Nothing is added to the air.
 */
class RawZigbeeTransport : public connectionlib::Transport {
  public:
    explicit RawZigbeeTransport(RawZigbeeParams params);

    RawZigbeeTransport(const RawZigbeeTransport &) = delete;
    RawZigbeeTransport &operator=(const RawZigbeeTransport &) = delete;

    bool setup() override;
    [[nodiscard]] int lastErrorCode() const override;
    connectionlib::SendResult send(const connectionlib::Address &destination,
                                    const uint8_t *payload, size_t len) override;
    std::optional<connectionlib::ReceivedMessage> poll() override;
    [[nodiscard]] connectionlib::Address broadcastAddress() const override;
    [[nodiscard]] size_t maxPayloadBytes() const override;
    [[nodiscard]] const char *technologyName() const override;
    [[nodiscard]] float currentDutyCyclePercent() const override;
    [[nodiscard]] uint32_t timeUntilNextAllowedMs(size_t payloadLen) const override;

  private:
    static constexpr size_t kMaxPayload = 84;   // XBee S2C ZB unfragmented limit
    static constexpr uint32_t kTxStatusWaitMs = 1500;
    static constexpr uint32_t kRssiQueryWaitMs = 50;

    connectionlib::ReceivedMessage buildReceived();
    std::optional<float> queryLastRssi();

    RawZigbeeParams params_;
    HardwareSerial serial_{2};
    XBee xbee_;
    int lastError_ = 0;
    std::optional<connectionlib::ReceivedMessage> pendingRx_;
};

}   // namespace bench

#endif   // defined(ESP32)
