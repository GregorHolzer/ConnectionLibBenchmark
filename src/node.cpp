/**
 * @brief Node firmware: measures the one point it was built for, prints it, and stops.
 *
 * Stop-and-wait ping-pong: ping N is sent once, and N+1 follows as soon as pong N is back or its
 * wait ran out. No retries, no pause, and nothing printed until the run is over. Two times are
 * taken per exchange, both from an instant *before* send():
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
 *   RUN,begin / RUN,end                    the active-energy window: first ping to last pong
 *   PPD,<seq>,<payload>,<total>,<send_us>,<rtt_us>,<status>,<ack>,<rssi>,<snr>,
 *       <gw_rx>,<gw_rssi>,<gw_snr>,<duty_pct>,<ok>          one per ping, printed after the run
 *   PPS,<sent>,<answered>,<gw_rx>,<elapsed_ms>,<duty_pct>,<aborted>
 *   RELEASE,ok | RELEASE,noack             LoRa only
 *   ERR,<reason>
 *   END                                    always last
 */
#include <optional>
#include <vector>

#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "NodeLoRa.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

constexpr uint32_t kMaxLossStreak = 10;   // this many losses in a row: out of range, stop

void printField(const std::optional<float> &value) {
    if (value) {
        Serial.printf(",%.1f", static_cast<double>(*value));
    } else {
        Serial.print(',');
    }
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

/** @brief Everything recorded about one ping, kept in RAM until the run is over. */
struct Exchange {
    SendResult sent;
    uint32_t sendUs, rttUs;
    bool answered;
    std::optional<float> rssiDbm, snrDb;   // the node's reading of the pong
    frames::PongFields gateway;            // what the pong carried back
    float dutyPercent;
};

void runExchanges(Transport &link) {
    const Address gateway = gatewayAddress(kTech);
    uint8_t ping[frames::kMaxBytes];
    std::vector<Exchange> log;
    log.reserve(kExchanges);   // allocated up front: nothing but the radio happens in the loop
    uint32_t lossStreak = 0;
    uint16_t gatewayRx = 0;

    // Between RUN,begin and RUN,end the node only sends, waits and notes the result. Each ping
    // follows the previous pong at once, and nothing is printed until the run is over, so the
    // serial port is neither in the timings nor in the energy window.
    Serial.println("RUN,begin");
    Serial.flush();
    uint32_t startMs = millis();
    for (uint32_t seq = 0; seq < kExchanges && lossStreak < kMaxLossStreak; seq++) {
        frames::build(ping, kPayloadBytes, 'P', static_cast<uint16_t>(seq), kRunId);
        Exchange x{};
        uint32_t t0 = micros();
        x.sent = link.send(gateway, ping, kPayloadBytes);
        x.sendUs = micros() - t0;
        std::optional<ReceivedMessage> pong;
        if (x.sent.status == SendStatus::Ok) {
            pong = await(link, 'Q', static_cast<uint16_t>(seq), kReplyWaitMs);
        }
        x.rttUs = pong ? micros() - t0 : 0;
        x.answered = pong.has_value();
        x.gateway = {gatewayRx, std::nullopt, std::nullopt};
        if (pong) {
            x.rssiDbm = pong->rssiDbm;
            x.snrDb = pong->snrDb;
            x.gateway = frames::pongFields(pong->payload);
            gatewayRx = x.gateway.gatewayRx;
        }
        x.dutyPercent = link.currentDutyCyclePercent();
        lossStreak = pong ? 0 : lossStreak + 1;
        log.push_back(x);
    }
    uint32_t elapsedMs = millis() - startMs;
    Serial.println("RUN,end");
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

    uint32_t answered = 0;
    for (size_t seq = 0; seq < log.size(); seq++) {
        const Exchange &x = log[seq];
        answered += x.answered ? 1 : 0;
        Serial.printf("PPD,%u,%u,%u,%lu,%lu,%d,%d", static_cast<unsigned>(seq),
                      static_cast<unsigned>(kPayloadBytes), static_cast<unsigned>(x.sent.totalBytes),
                      static_cast<unsigned long>(x.sendUs), static_cast<unsigned long>(x.rttUs),
                      static_cast<int>(x.sent.status), x.sent.acknowledged ? 1 : 0);
        printField(x.rssiDbm);
        printField(x.snrDb);
        Serial.printf(",%u", x.gateway.gatewayRx);
        printField(x.gateway.rssiDbm);
        printField(x.gateway.snrDb);
        Serial.printf(",%.4f,%d\n", static_cast<double>(x.dutyPercent), x.answered ? 1 : 0);
    }
    Serial.printf("PPS,%u,%lu,%u,%lu,%.4f,%d\n", static_cast<unsigned>(log.size()),
                  static_cast<unsigned long>(answered), gatewayRx,
                  static_cast<unsigned long>(elapsedMs),
                  static_cast<double>(link.currentDutyCyclePercent()), aborted ? 1 : 0);
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
