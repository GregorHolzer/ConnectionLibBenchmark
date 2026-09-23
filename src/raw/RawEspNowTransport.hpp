#pragma once

#include <ConnectionLib/Transport.hpp>

#if defined(ESP32)

#include <array>
#include <atomic>

#include <esp_now.h>
#include <esp_wifi_types.h>

#include "raw/RawTransports.hpp"

namespace bench {

/**
 * @brief ESP-NOW with esp_now.h called directly and no ConnectionLib in the path.
 *
 * Expected to come out within noise of EspNowTransport, because ESP-NOW addresses natively and the
 * library's backend is a thin wrapper over it: there is no framing to strip, only an Address
 * translation and a ring buffer. That near-zero result is worth measuring rather than asserting —
 * "the abstraction is free here" is exactly the kind of claim that goes unchecked.
 *
 * Single-instance for the same reason the library's backend is: esp_now's C callbacks carry no
 * user-data pointer, so dispatch goes through a static instance pointer.
 */
class RawEspNowTransport : public connectionlib::Transport {
  public:
    explicit RawEspNowTransport(RawEspNowParams params);
    ~RawEspNowTransport() override;

    RawEspNowTransport(const RawEspNowTransport &) = delete;
    RawEspNowTransport &operator=(const RawEspNowTransport &) = delete;

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
    static constexpr size_t kMaxPayload = 250;
    static constexpr size_t kQueueCapacity = 8;

    struct QueuedMessage {
        std::array<uint8_t, 6> mac{};
        std::array<uint8_t, kMaxPayload> data{};
        uint8_t len = 0;
        uint32_t timestampMicros = 0;
        bool hasRssi = false;
        int8_t rssiDbm = 0;
    };

    static void onSniff(void *buf, wifi_promiscuous_pkt_type_t type);
    static void onRecv(const uint8_t *mac, const uint8_t *data, int len);
    static void onSent(const uint8_t *mac, esp_now_send_status_t status);

    RawEspNowParams params_;
    int lastError_ = 0;

    std::array<QueuedMessage, kQueueCapacity> queue_{};
    std::atomic<uint8_t> queueHead_{0};
    std::atomic<uint8_t> queueTail_{0};

    volatile bool sniffValid_ = false;
    volatile int8_t sniffRssiDbm_ = 0;
    std::array<uint8_t, 6> sniffMac_{};

    volatile bool sendDone_ = false;
    volatile esp_now_send_status_t sendStatus_ = ESP_NOW_SEND_FAIL;

    static RawEspNowTransport *activeInstance_;
};

}   // namespace bench

#endif   // defined(ESP32)
