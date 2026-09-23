/** @brief LEASE and RELEASE, the field node's control verbs. See FieldNode.hpp. */

// Compiled into the field-node build only, so the bench firmware stays exactly as it was.
#if defined(BENCH_FIELD_NODE)

#include "FieldNode.hpp"

#include <cstdlib>
#include <cstring>
#include <optional>

#include <Arduino.h>
#include <esp_mac.h>

#include "BenchCommands.hpp"
#include "BenchExchange.hpp"
#include "BenchState.hpp"
#include "Conformance.hpp"
#include "FieldProtocol.hpp"
#include "TransportFactory.hpp"

using namespace connectionlib;

namespace bench::field {

namespace {

// The ack is an 8-byte frame at SF12, ~1.5 s of airtime after the request (itself ~3 s, but send()
// has already blocked through that), with margin for the gateway's processing.
constexpr uint32_t kLeaseAckWaitMs = 5000;

// Time for the gateway to tear home down and bring the point's transport up after acking, before
// this board starts on the point. ESP-NOW is the slowest to come up of the three.
constexpr uint32_t kSwitchGuardMs = 1000;

constexpr int kReleaseAttempts = 3;

struct ActiveLease {
    bool valid = false;
    uint32_t campaignId = 0;
    uint16_t pointId = 0;
};

ActiveLease g_active;

/** @brief Poll until a token of `kind` for the active lease arrives, or `waitMs` runs out. */
std::optional<Token> awaitToken(Transport &transport, uint8_t kind, uint32_t waitMs,
                                std::optional<float> *rssi = nullptr,
                                std::optional<float> *snr = nullptr) {
    uint32_t start = millis();
    while (millis() - start < waitMs) {
        auto received = transport.poll();
        Token token;
        if (received.has_value() &&
            decodeToken(received->payload.data(), received->payload.size(), kind, token) &&
            token.campaignId == g_active.campaignId && token.pointId == g_active.pointId) {
            if (rssi) *rssi = received->rssiDbm;
            if (snr) *snr = received->snrDb;
            return token;
        }
        delay(1);
    }
    return std::nullopt;
}

void printOptionalField(const std::optional<float> &value) {
    Serial.print(',');
    detail::printOptional(value);
}

}   // namespace

void handleLease(char *args) {
    uint32_t campaignId = 0;
    long pointId = -1;
    long leaseSeconds = 0;
    BenchConfig config;   // a complete specification, as with CFG
    for (char *token = strtok(args, " \t"); token != nullptr; token = strtok(nullptr, " \t")) {
        char *equals = strchr(token, '=');
        if (!equals) {
            continue;
        }
        *equals = '\0';
        const char *value = equals + 1;
        if (strcmp(token, "cid") == 0) campaignId = strtoul(value, nullptr, 16);
        else if (strcmp(token, "pt") == 0) pointId = strtol(value, nullptr, 10);
        else if (strcmp(token, "lease") == 0) leaseSeconds = strtol(value, nullptr, 10);
        else applyKeyValue(config, token, value);
    }
    if (pointId < 0 || pointId > 0xFFFF || leaseSeconds <= 0 || leaseSeconds > 0xFFFF) {
        Serial.println("ERR,LEASE needs pt=0..65535 and lease=1..65535");
        return;
    }
    // Refuse here as well as on the gateway: a config the gateway would reject costs SF12 airtime
    // for nothing.
    if (const char *reason = checkConformance(config)) {
        Serial.printf("ERR,%s\n", reason);
        return;
    }

    State &s = state();
    g_active = ActiveLease{true, campaignId, static_cast<uint16_t>(pointId)};
    if (!applyConfiguration(homeConfig(), false)) {
        Serial.println("ERR,home setup failed");
        return;
    }

    LeaseRequest request;
    request.campaignId = campaignId;
    request.pointId = static_cast<uint16_t>(pointId);
    request.leaseSeconds = static_cast<uint16_t>(leaseSeconds);
    request.config = config;
    // The gateway needs *this* board's MAC as its ESP-NOW peer; config.peerMac is the gateway's.
    esp_read_mac(request.config.peerMac, ESP_MAC_WIFI_STA);

    uint8_t frame[kLeaseFrameBytes];
    encodeLease(request, frame);
    SendResult sent = s.transport->send(peerAddress(s.config), frame, sizeof(frame));
    if (sent.status == SendStatus::DutyCycleExceeded) {
        Serial.println("ERR,lease duty cycle");
        return;
    }
    if (sent.status != SendStatus::Ok) {
        Serial.printf("ERR,lease send failed (status %d)\n", static_cast<int>(sent.status));
        return;
    }

    std::optional<float> rssi;
    std::optional<float> snr;
    auto ack = awaitToken(*s.transport, kLeaseAckKind, kLeaseAckWaitMs, &rssi, &snr);
    if (!ack.has_value()) {
        Serial.println("ERR,lease noack");
        return;
    }
    if (ack->status != kStatusOk) {
        Serial.println("ERR,lease rejected by gateway");
        return;
    }

    // Home's signal quality at this site, per point, at no cost: the ack had to arrive anyway.
    Serial.print("LEASE,ok");
    printOptionalField(rssi);
    printOptionalField(snr);
    Serial.println();

    delay(kSwitchGuardMs);
    applyConfiguration(config, true);   // PPH header and ACK, exactly as CFG
}

void handleRelease() {
    State &s = state();
    bool released = false;
    if (g_active.valid && s.configured && s.transport) {
        Token token;
        token.kind = kReleaseKind;
        token.campaignId = g_active.campaignId;
        token.pointId = g_active.pointId;
        uint8_t frame[kTokenFrameBytes];
        encodeToken(token, frame);

        for (int attempt = 0; attempt < kReleaseAttempts && !released; attempt++) {
            if (s.transport->send(peerAddress(s.config), frame, sizeof(frame)).status != SendStatus::Ok) {
                continue;
            }
            released = awaitToken(*s.transport, kReleaseAckKind, s.config.replyWaitMs).has_value();
        }
    }
    g_active.valid = false;
    Serial.println(released ? "RELEASE,ok" : "RELEASE,noack");
    Serial.println("RDY");
}

}   // namespace bench::field

#endif   // BENCH_FIELD_NODE
