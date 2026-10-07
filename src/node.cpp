/**
 * @brief Node firmware: measures the one point it was built for, prints it, and stops.
 *
 * The measurement is stop-and-wait ping-pong. The node sends ping N once, waits for the pong with
 * the same N, and only then sends N+1; there are no retries, so delivery is simply counted. Two
 * times are taken per exchange, both from an instant *before* send():
 *
 *   send_us   time blocked inside send()   (outbound airtime plus the radio's own status wait)
 *   rtt_us    until the matching pong is back in the application
 *
 * Records on serial, parsed by bench/records.py:
 *
 *   READY                                  booted; waits here for the host to send 'G'
 *   PPH,<key>,<value>                      what this build is
 *   HOME,ok,<rssi>,<snr>,<attempts> | HOME,noack     LoRa only: the switch handshake on home
 *   IDLE,begin / IDLE,end,<ms>             the standing-current window
 *   RUN,begin                              the active-energy window opens; it closes at PPS
 *   PPD,<seq>,<payload>,<total>,<send_us>,<rtt_us>,<status>,<ack>,<rssi>,<snr>,
 *       <gw_rx>,<gw_rssi>,<gw_snr>,<duty_pct>,<ok>          one per ping
 *   PPS,<sent>,<answered>,<gw_rx>,<elapsed_ms>,<duty_pct>,<aborted>
 *   RELEASE,ok | RELEASE,noack             LoRa only
 *   ERR,<reason>
 *   END                                    always last
 */
#include <optional>

#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

// listen(0) in a 200 us loop, not listen(waitMs): the library's blocking listen() sleeps in 1 ms
// steps, which would quantise ESP-NOW's ~3.5 ms round trip into the harness's own ticks.
constexpr uint32_t kPollIntervalUs = 200;
constexpr uint32_t kMaxLossStreak = 10;     // this many losses in a row: out of range, stop
constexpr uint32_t kHomeAckWaitMs = 5000;   // the ack is ~1.5 s of SF12 airtime
constexpr uint32_t kSwitchGuardMs = 1000;   // the gateway rebuilds its radio after acking
constexpr int kControlAttempts = 3;

std::optional<ReceivedMessage> await(Transport &link, char kind, uint16_t seq, uint32_t waitMs) {
    uint32_t start = millis();
    while (millis() - start < waitMs) {
        auto message = link.listen(0);
        if (message && frames::matches(message->payload, kind, seq, kRunId)) {
            return message;
        }
        delayMicroseconds(kPollIntervalUs);
    }
    return std::nullopt;
}

void printField(const std::optional<float> &value) {
    if (value) {
        Serial.printf(",%.1f", static_cast<double>(*value));
    } else {
        Serial.print(',');
    }
}

/** @brief Send one header-only control frame and wait for the frame that answers it. */
std::optional<ReceivedMessage> control(Transport &link, const uint8_t *frame, size_t len,
                                       char answer, uint16_t seq, uint32_t waitMs) {
    if (link.send(gatewayAddress(Tech::LoRa), frame, len).status != SendStatus::Ok) {
        return std::nullopt;
    }
    return await(link, answer, seq, waitMs);
}

/**
 * @brief Tell the gateway, on home, which LoRa point comes next, then move `link` to that point.
 *
 * A missing ack is ambiguous: either the request was lost and the gateway is still on home, or
 * only the ack was lost and it has already moved. So each attempt ends with a probe on the point
 * itself, and an answer there counts as success.
 */
bool negotiate(std::unique_ptr<Transport> &link) {
    uint8_t frame[frames::kSwitchBytes];
    frames::build(frame, sizeof(frame), 'C', 0, kRunId);
    frame[frames::kSf] = kPoint.spreadingFactor;
    frame[frames::kCr] = kPoint.codingRate;
    frame[frames::kPower] = static_cast<uint8_t>(kPoint.txPowerDbm);
    frame[frames::kPreamble] = kPoint.preambleLength;
    frame[frames::kBwKHz] = kPoint.bandwidthKHz;
    frame[frames::kLease4s] = static_cast<uint8_t>((kLeaseSeconds + 3) / 4);

    uint8_t probe[frames::kHeaderBytes];
    frames::build(probe, sizeof(probe), 'P', frames::kProbeSeq, kRunId);

    for (int attempt = 0; attempt < kControlAttempts; attempt++) {
        link.reset();   // one radio: the old transport goes before the new one comes up
        link = makeLoRa(kHome);
        auto ack = link->setup() ? control(*link, frame, sizeof(frame), 'A', 0, kHomeAckWaitMs)
                                 : std::nullopt;
        if (ack) {
            Serial.print("HOME,ok");
            printField(ack->rssiDbm);
            printField(ack->snrDb);
            Serial.printf(",%d\n", attempt + 1);
        }
        delay(kSwitchGuardMs);
        link.reset();
        link = makeLoRa(kPoint);
        if (!link->setup()) {
            return false;
        }
        if (ack || control(*link, probe, sizeof(probe), 'Q', frames::kProbeSeq, kReplyWaitMs)) {
            return true;
        }
    }
    Serial.println("HOME,noack");
    return false;
}

void holdIdle(Transport *link) {
    Serial.println("IDLE,begin");
    uint32_t start = millis();
    while (millis() - start < kIdleMs) {
        if (link) {
            link->listen(0);   // stay in the receive state a deployed node would be in
        }
        delay(1);
    }
    Serial.printf("IDLE,end,%lu\n", static_cast<unsigned long>(millis() - start));
}

void runExchanges(Transport &link) {
    const Address gateway = gatewayAddress(kTech);
    uint8_t ping[frames::kMaxBytes];
    uint32_t sent = 0, answered = 0, lossStreak = 0;
    uint16_t gatewayRx = 0;

    Serial.println("RUN,begin");
    uint32_t startMs = millis();
    for (uint32_t seq = 0; seq < kExchanges && lossStreak < kMaxLossStreak; seq++) {
        delay(kSpacingMs);
        frames::build(ping, kPayloadBytes, 'P', static_cast<uint16_t>(seq), kRunId);

        uint32_t t0 = micros();
        SendResult result = link.send(gateway, ping, kPayloadBytes);
        uint32_t sendUs = micros() - t0;
        std::optional<ReceivedMessage> pong;
        if (result.status == SendStatus::Ok) {
            pong = await(link, 'Q', static_cast<uint16_t>(seq), kReplyWaitMs);
        }
        uint32_t rttUs = pong ? micros() - t0 : 0;

        sent++;
        answered += pong ? 1 : 0;
        lossStreak = pong ? 0 : lossStreak + 1;
        frames::PongFields fromGateway{gatewayRx, std::nullopt, std::nullopt};
        if (pong) {
            fromGateway = frames::pongFields(pong->payload);
            gatewayRx = fromGateway.gatewayRx;
        }

        Serial.printf("PPD,%lu,%u,%u,%lu,%lu,%d,%d", static_cast<unsigned long>(seq),
                      static_cast<unsigned>(kPayloadBytes), static_cast<unsigned>(result.totalBytes),
                      static_cast<unsigned long>(sendUs), static_cast<unsigned long>(rttUs),
                      static_cast<int>(result.status), result.acknowledged ? 1 : 0);
        printField(pong ? pong->rssiDbm : std::nullopt);
        printField(pong ? pong->snrDb : std::nullopt);
        Serial.printf(",%u", fromGateway.gatewayRx);
        printField(fromGateway.rssiDbm);
        printField(fromGateway.snrDb);
        Serial.printf(",%.4f,%d\n", static_cast<double>(link.currentDutyCyclePercent()), pong ? 1 : 0);
    }
    uint32_t elapsedMs = millis() - startMs;
    bool aborted = lossStreak >= kMaxLossStreak;

    // If the last pongs were lost the gateway's count is stale. Ask for it, outside the window.
    frames::build(ping, frames::kHeaderBytes, 'P', frames::kProbeSeq, kRunId);
    for (int attempt = 0; attempt < kControlAttempts && !aborted; attempt++) {
        if (link.send(gateway, ping, frames::kHeaderBytes).status != SendStatus::Ok) {
            continue;
        }
        if (auto pong = await(link, 'Q', frames::kProbeSeq, kReplyWaitMs)) {
            gatewayRx = frames::pongFields(pong->payload).gatewayRx;
            break;
        }
    }
    Serial.printf("PPS,%lu,%lu,%u,%lu,%.4f,%d\n", static_cast<unsigned long>(sent),
                  static_cast<unsigned long>(answered), gatewayRx,
                  static_cast<unsigned long>(elapsedMs),
                  static_cast<double>(link.currentDutyCyclePercent()), aborted ? 1 : 0);
}

/** @brief Send the gateway back to home now, instead of letting it wait out the lease. */
void release(Transport &link) {
    uint8_t frame[frames::kHeaderBytes];
    frames::build(frame, sizeof(frame), 'R', 0, kRunId);
    for (int attempt = 0; attempt < kControlAttempts; attempt++) {
        if (control(link, frame, sizeof(frame), 'K', 0, kReplyWaitMs)) {
            Serial.println("RELEASE,ok");
            return;
        }
    }
    Serial.println("RELEASE,noack");
}

}   // namespace

void setup() {
    Serial.begin(115200);
    delay(500);
    board::powerUp(kTech != Tech::EspNow);
    // Measure only when the host says so. A board that was merely powered up or reset must not
    // start a LoRa handshake nobody is recording or charging to the duty cycle.
    Serial.println("READY");
    while (Serial.read() != 'G') {
        delay(10);
    }
    Serial.printf("PPH,tech,%s\nPPH,runId,%u\n", kTechName, kRunId);

    std::unique_ptr<Transport> link;
    bool ready = true;
    if (kTech == Tech::LoRa && kExchanges > 0) {
        ready = negotiate(link);
    } else if (kTech != Tech::None) {
        link = makeTransport(kTech);
        ready = link->setup();
    }

    if (!ready) {
        Serial.printf("ERR,no link (code %d)\n", link ? link->lastErrorCode() : 0);
    } else {
        if (link) {
            Serial.printf("PPH,technologyName,%s\n", link->technologyName());
        }
        if (kIdleMs > 0) {
            holdIdle(link.get());
        }
        if (link && kExchanges > 0) {
            runExchanges(*link);
            if (kTech == Tech::LoRa) {
                release(*link);
            }
        }
    }
    Serial.println("END");
}

void loop() {
    delay(1000);
}
