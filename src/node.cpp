/**
 * @brief Node firmware: measures the one point it was built for, prints it, and stops.
 *
 * A one-way transfer, as fast as the link allows. The loop is nothing but
 *
 *     result = link.send(gateway, payload)
 *
 * repeated. send() returns once the library has its confirmation of delivery (ESP-NOW's MAC ack,
 * Zigbee's APS ack, ConnectionLib's software ack on LoRa) or has given up waiting for it. The
 * time spent in send() is the delivery latency; `acknowledged` is the delivery. No retries, no
 * pause, and nothing is printed until the run is over.
 *
 * Records on serial, parsed by bench/records.py:
 *
 *   READY                                  booted; waits here for the host to send 'G'
 *   PPH,<key>,<value>                      what this build is
 *   HOME,ok,<attempts> | HOME,noack        LoRa only: the gateway was moved to this point
 *   IDLE,begin / IDLE,end,<ms>             the standing-current window
 *   RUN,begin / RUN,end                    the active-energy window: the loop and nothing else
 *   PPD,<seq>,<payload>,<total>,<latency_us>,<status>,<acked>,<duty_pct>     one per payload
 *   PPS,<sent>,<acked>,<elapsed_ms>,<duty_pct>,<aborted>
 *   REPORT,ok,<gw_received>,<gw_rssi>,<gw_snr> | REPORT,lost | REPORT,noack
 *   ERR,<reason>
 *   END                                    always last
 */
#include <vector>

#include <Arduino.h>

#include "Board.hpp"
#include "Frames.hpp"
#include "NodeControl.hpp"
#include "Transports.hpp"

using namespace connectionlib;
using namespace bench;

namespace {

constexpr uint32_t kMaxLossStreak = 10;   // this many losses in a row: out of range, stop

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

/** @brief What is kept per payload, in RAM, until the run is over. */
struct Sample {
    SendResult result;
    uint32_t latencyUs;
    float dutyPercent;
};

void runTransfer(Transport &link) {
    const Address gateway = gatewayAddress(kTech);
    uint8_t payload[frames::kMaxBytes];
    std::vector<Sample> samples;
    samples.reserve(kExchanges);   // allocated up front: nothing but the radio happens in the loop
    uint32_t lossStreak = 0;

    Serial.println("RUN,begin");
    Serial.flush();
    uint32_t startMs = millis();
    for (uint32_t seq = 0; seq < kExchanges && lossStreak < kMaxLossStreak; seq++) {
        frames::build(payload, kPayloadBytes, 'D', static_cast<uint16_t>(seq), kRunId);
        uint32_t t0 = micros();
        SendResult result = link.send(gateway, payload, kPayloadBytes);
        uint32_t latencyUs = micros() - t0;
        lossStreak = result.acknowledged ? 0 : lossStreak + 1;
        samples.push_back({result, latencyUs, link.currentDutyCyclePercent()});
    }
    uint32_t elapsedMs = millis() - startMs;
    Serial.println("RUN,end");

    uint32_t acked = 0;
    for (size_t seq = 0; seq < samples.size(); seq++) {
        const Sample &s = samples[seq];
        acked += s.result.acknowledged ? 1 : 0;
        Serial.printf("PPD,%u,%u,%u,%lu,%d,%d,%.4f\n", static_cast<unsigned>(seq),
                      static_cast<unsigned>(kPayloadBytes), static_cast<unsigned>(s.result.totalBytes),
                      static_cast<unsigned long>(s.latencyUs), static_cast<int>(s.result.status),
                      s.result.acknowledged ? 1 : 0, static_cast<double>(s.dutyPercent));
    }
    Serial.printf("PPS,%u,%lu,%lu,%.4f,%d\n", static_cast<unsigned>(samples.size()),
                  static_cast<unsigned long>(acked), static_cast<unsigned long>(elapsedMs),
                  static_cast<double>(link.currentDutyCyclePercent()),
                  lossStreak >= kMaxLossStreak ? 1 : 0);
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
            runTransfer(*link);
            collectReport(*link);
        }
    }
    Serial.println("END");
}

void loop() {
    delay(1000);
}
