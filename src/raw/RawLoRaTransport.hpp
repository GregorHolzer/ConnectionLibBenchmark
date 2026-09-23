#pragma once

#include <ConnectionLib/Transport.hpp>

// The no-library LoRa arm: RadioLib's SX1278 driven directly, with none of ConnectionLib's framing
// or addressing in the path. See RawTransports.hpp for what this arm is and is not measuring.
#if defined(ESP32)

#include <RadioLib.h>
#include <SPI.h>

#include "ConnectionLib/DutyCycleGuard.hpp"
#include "raw/RawTransports.hpp"

namespace bench {

/**
 * @brief LoRa with no ConnectionLib framing: the payload goes on the air exactly as handed over.
 *
 * This is the arm that should differ. ConnectionLib prepends a 6-byte FrameHeader
 * (dest/src/seq/payloadLen) to every LoRa packet because a raw SX1278 has no addressing of its
 * own. Those bytes cost time-on-air, and time-on-air costs energy and duty-cycle budget, so the
 * delta against LoRaTransport measures what the library charges for addressing on this radio.
 *
 * Time-on-air is quantised into symbols, so the cost is a near-constant +28.7 ms per transmission
 * at SF9/BW125/CR4:7 rather than a fixed percentage: 14.5 % at an 8-byte payload, 4.2 % at 84.
 * (The 43 % figure that appears elsewhere is the header's share of *bytes on air*, which is a
 * different and larger number than its share of airtime — worth keeping distinct.)
 *
 * Dropping the header is only possible because the benchmark's own wire format already carries a
 * kind byte and a sequence number (see BenchRunner.hpp), so a ping is still distinguishable from a
 * pong without the library's help. Nothing is reintroduced to compensate.
 *
 * The duty-cycle guard stays. It is a regulatory control rather than a library feature under test,
 * and docs/regulations.md is explicit that the EU envelope is never relaxed for benchmarking
 * convenience. The raw-versus-library delta therefore measures framing and addressing, not
 * duty-cycle logic.
 */
class RawLoRaTransport : public connectionlib::Transport {
  public:
    explicit RawLoRaTransport(RawLoRaParams params);

    RawLoRaTransport(const RawLoRaTransport &) = delete;
    RawLoRaTransport &operator=(const RawLoRaTransport &) = delete;

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
    static constexpr size_t kMaxRadioPacket = 255;

    RawLoRaParams params_;
    connectionlib::DutyCycleGuard dutyCycle_;
    SPIClass spi_;
    mutable SX1278 radio_;   // getTimeOnAir() is non-const in RadioLib
    int lastError_ = 0;
};

}   // namespace bench

#endif   // defined(ESP32)
