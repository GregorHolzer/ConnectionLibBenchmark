/**
 * @brief Node role, plus the null measurement - the two paths that are not the gateway's loop.
 *
 * nodeTick() counts *distinct* sequence numbers, which is what makes receiver-side PDR measurable:
 * the sender's view of delivery means three different things across these three radios.
 *
 * runNullMeasurement() is the harness's own per-exchange cost with no radio involved (practice
 * P4). It lives here rather than beside the gateway loop because it deliberately shares none of
 * that loop's code - it re-does the same steps so the figure is the floor under every latency
 * number, not an estimate of it.
 */

#include "BenchRunner.hpp"

#include <algorithm>

#include <Arduino.h>

#include "BenchExchange.hpp"

using namespace connectionlib;
using namespace bench::detail;

namespace bench {

void nodeTick(Transport &transport) {
    auto received = transport.poll();
    if (received.has_value() && countPing(*received)) {
        replyToPing(transport, *received);
    }
}

bool countPing(const ReceivedMessage &received) {
    const auto &payload = received.payload;
    if (payload.size() < kMinPayloadBytes || payload[0] != static_cast<uint8_t>('P')) {
        return false;   // not one of our pings
    }
    uint16_t seq = sequenceOf(payload);

    counters().rxTotal++;
    if (markSeen(seq)) {
        counters().rxDistinct++;
    }

    Serial.printf("PPN,%u,%u,%u,", static_cast<unsigned>(seq), static_cast<unsigned>(payload.size()),
                   static_cast<unsigned>(received.totalBytes));
    printOptional(received.rssiDbm);
    Serial.print(',');
    printOptional(received.snrDb);
    Serial.println();
    return true;
}

void replyToPing(Transport &transport, const ReceivedMessage &received) {
    const auto &payload = received.payload;
    uint16_t seq = sequenceOf(payload);

    // Reply is the same size as the ping, so both legs carry an identical payload.
    uint8_t reply[kMaxPayloadBytes];
    size_t replyLen = std::min(payload.size(), kMaxPayloadBytes);
    buildMessage(reply, replyLen, 'Q', seq);

    SendResult result = transport.send(received.source, reply, replyLen);
    if (result.status == SendStatus::Ok) {
        counters().repliesOk++;
    } else {
        counters().replyFailures++;
    }

    Serial.printf("PPM,%lu,%lu,%lu,%lu\n", static_cast<unsigned long>(counters().rxDistinct),
                   static_cast<unsigned long>(counters().rxTotal), static_cast<unsigned long>(counters().repliesOk),
                   static_cast<unsigned long>(counters().replyFailures));
}

void runNullMeasurement(const BenchConfig &config) {
    // Everything the gateway loop does per exchange except touching a radio: the two micros()
    // reads, the payload construction, and one pass of the poll-wait body. Whatever this costs is
    // the floor under every latency number the suite reports.
    uint8_t buf[kMaxPayloadBytes];
    size_t payloadLen = std::min(std::max(static_cast<size_t>(config.payloadBytes), kMinPayloadBytes),
                                  kMaxPayloadBytes);

    for (uint32_t i = 0; i < config.exchanges; i++) {
        uint32_t t0 = micros();
        buildMessage(buf, payloadLen, 'P', static_cast<uint16_t>(i));
        uint32_t overheadUs = micros() - t0;
        Serial.printf("PPD,%lu,1,%u,%u,%lu,%lu,0,0,,,0.0000,1\n", static_cast<unsigned long>(i),
                       static_cast<unsigned>(payloadLen), static_cast<unsigned>(payloadLen),
                       static_cast<unsigned long>(overheadUs), static_cast<unsigned long>(overheadUs));
    }
    Serial.printf("PPS,%lu,%lu,0,0,0.0000,0,0\n", static_cast<unsigned long>(config.exchanges),
                   static_cast<unsigned long>(config.exchanges));
}
}   // namespace bench
