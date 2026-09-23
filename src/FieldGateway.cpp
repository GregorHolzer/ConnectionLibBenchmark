/** @brief The field gateway's lease state machine and flash log. See FieldGateway.hpp. */

// Compiled into the field-gateway build only, so the bench firmware stays exactly as it was.
#if defined(BENCH_FIELD_GATEWAY)

#include "FieldGateway.hpp"

#include <cstdio>

#include <Arduino.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include "BenchCommands.hpp"
#include "BenchExchange.hpp"
#include "BenchReporting.hpp"
#include "BenchRunner.hpp"
#include "BenchState.hpp"
#include "Conformance.hpp"
#include "FieldProtocol.hpp"

using namespace connectionlib;

namespace bench::field {

namespace {

constexpr const char *kLogPath = "/field.log";

// Long enough for the slowest blocking call the gateway makes - an SF12 transmit of an 84-byte
// pong is 4.8 s - with a wide margin. A hang shorter than this was never going to matter.
constexpr uint32_t kWatchdogSeconds = 60;

// How long to wait before retrying home after it failed to come up.
constexpr uint32_t kHomeRetryMs = 5000;

/**
 * @brief 10 % of a rolling hour of LoRa airtime, kept by the application rather than the transport.
 *
 * LoRaTransport's own DutyCycleGuard is rebuilt with every Transport, so it forgets everything at
 * each lease boundary. On the bench the host's ledger covers that gap; out here nothing does, so
 * this one spans the whole uptime. Ten-second buckets keep it to a fixed 2.9 KB however many
 * packets an hour holds - at SF7 that is thousands.
 */
class AirtimeLedger {
  public:
    bool allows(uint32_t nowMs, uint32_t estimateMs) const {
        return usedMs(nowMs) + estimateMs <= kLimitMs;
    }

    void record(uint32_t nowMs, uint32_t durationMs) {
        uint32_t epoch = nowMs / kBucketMs;
        size_t slot = epoch % kBuckets;
        if (epochs_[slot] != epoch) {
            epochs_[slot] = epoch;
            usedMs_[slot] = 0;
        }
        usedMs_[slot] += durationMs;
        lastMs_ = durationMs;
    }

    /** @brief The last transmission's airtime, as the estimate for the next one. */
    uint32_t lastMs() const { return lastMs_; }

  private:
    static constexpr uint32_t kBucketMs = 10000;
    static constexpr size_t kBuckets = 360;           // one hour
    static constexpr uint32_t kLimitMs = 360000;      // 10 % of it, docs/regulations.md

    uint32_t usedMs(uint32_t nowMs) const {
        uint32_t epoch = nowMs / kBucketMs;
        uint32_t total = 0;
        for (size_t i = 0; i < kBuckets; i++) {
            if (epoch - epochs_[i] < kBuckets) {
                total += usedMs_[i];
            }
        }
        return total;
    }

    uint32_t epochs_[kBuckets] = {};
    uint32_t usedMs_[kBuckets] = {};
    // Before anything has been sent, assume the worst: an SF12 lease request is ~3 s.
    uint32_t lastMs_ = 3000;
};

enum class Phase : uint8_t { Home, Leased };

struct Lease {
    uint32_t campaignId = 0;
    uint16_t pointId = 0;
    uint32_t startMs = 0;
    uint32_t durationMs = 0;
    uint32_t dutyDropped = 0;
};

AirtimeLedger g_ledger;
Phase g_phase = Phase::Home;
Lease g_lease;
uint32_t g_boot = 0;
bool g_logReady = false;
uint32_t g_homeFailedAtMs = 0;
bool g_homeUp = false;

bool onLoRa() {
    return state().config.tech == Tech::LoRa;
}

/**
 * @brief Send one frame on the current transport, through the airtime ledger when it is LoRa.
 *
 * Returns false without transmitting when the ledger has no room. Every LoRa transmission the
 * gateway makes goes through here, control and pongs alike, which is what lets the ledger stand in
 * for the host's.
 */
bool sendLedgered(const Address &to, const uint8_t *data, size_t len) {
    Transport &transport = *state().transport;
    if (!onLoRa()) {
        return transport.send(to, data, len).status == SendStatus::Ok;
    }
    if (!g_ledger.allows(millis(), g_ledger.lastMs())) {
        return false;
    }
    uint32_t t0 = millis();
    bool ok = transport.send(to, data, len).status == SendStatus::Ok;
    g_ledger.record(millis(), millis() - t0);
    return ok;
}

void sendToken(const Address &to, uint8_t kind, uint32_t campaignId, uint16_t pointId, uint8_t status) {
    Token token;
    token.kind = kind;
    token.campaignId = campaignId;
    token.pointId = pointId;
    token.status = status;
    uint8_t frame[kTokenFrameBytes];
    encodeToken(token, frame);
    sendLedgered(to, frame, sizeof(frame));
}

void appendLog(const char *line) {
    if (!g_logReady) {
        return;
    }
    File file = LittleFS.open(kLogPath, FILE_APPEND);
    if (!file) {
        return;
    }
    file.println(line);
    file.close();
}

void enterHome() {
    g_phase = Phase::Home;
    g_homeUp = applyConfiguration(homeConfig(), false);
    if (!g_homeUp) {
        g_homeFailedAtMs = millis();
    }
}

/**
 * @brief Append the finished lease's record and go back to home.
 *
 * `FLP,<boot>,<campaign>,<point>,<tech>,<stack>,<leaseMs>,<heldMs>,<reason>,<rxDistinct>,
 *  <rxTotal>,<repliesOk>,<replyFailures>,<dutyDropped>,<bitmap>`
 *
 * The bitmap is the distinct-arrival bitmap as 8-hex-digit words, word 0 first, cut after the last
 * non-zero word. Written here - after the node's RUN has finished and before home comes back up -
 * so a flash write never lands inside a measured window.
 */
void endLease(const char *reason) {
    const detail::NodeCounters &c = detail::counters();
    char line[96 + 8 * 128];   // fixed fields plus the whole bitmap, 128 words of 8 hex digits
    int n = snprintf(line, sizeof(line), "FLP,%lu,%08lx,%u,%s,%s,%lu,%lu,%s,%lu,%lu,%lu,%lu,%lu,",
                     static_cast<unsigned long>(g_boot), static_cast<unsigned long>(g_lease.campaignId),
                     static_cast<unsigned>(g_lease.pointId), techName(state().config.tech),
                     state().config.stack == Stack::Raw ? "raw" : "lib",
                     static_cast<unsigned long>(g_lease.durationMs),
                     static_cast<unsigned long>(millis() - g_lease.startMs), reason,
                     static_cast<unsigned long>(c.rxDistinct), static_cast<unsigned long>(c.rxTotal),
                     static_cast<unsigned long>(c.repliesOk), static_cast<unsigned long>(c.replyFailures),
                     static_cast<unsigned long>(g_lease.dutyDropped));

    const uint32_t *words = detail::seenBitmap();
    size_t used = detail::seenBitmapWords();
    while (used > 0 && words[used - 1] == 0) {
        used--;
    }
    for (size_t i = 0; i < used && n > 0 && static_cast<size_t>(n) + 9 < sizeof(line); i++) {
        n += snprintf(line + n, sizeof(line) - n, "%08lx", static_cast<unsigned long>(words[i]));
    }
    appendLog(line);
    enterHome();
}

void startLease(const LeaseRequest &request, const Address &from) {
    if (checkConformance(request.config) != nullptr) {
        sendToken(from, kLeaseAckKind, request.campaignId, request.pointId, kStatusRejected);
        return;
    }
    // Ack before switching: once the point's transport is up, home is gone.
    sendToken(from, kLeaseAckKind, request.campaignId, request.pointId, kStatusOk);

    g_lease = Lease{};
    g_lease.campaignId = request.campaignId;
    g_lease.pointId = request.pointId;
    g_lease.durationMs = static_cast<uint32_t>(request.leaseSeconds) * 1000;
    g_lease.startMs = millis();
    g_phase = Phase::Leased;

    // applyConfiguration() also clears the arrival counters and bitmap for the new point.
    if (!applyConfiguration(request.config, false)) {
        endLease("setupfail");
    }
}

void tickHome() {
    if (!g_homeUp) {
        if (millis() - g_homeFailedAtMs >= kHomeRetryMs) {
            enterHome();
        }
        return;
    }
    auto received = state().transport->poll();
    if (!received.has_value()) {
        return;
    }
    LeaseRequest request;
    if (decodeLease(received->payload.data(), received->payload.size(), request)) {
        startLease(request, received->source);
    }
}

void tickLeased() {
    if (millis() - g_lease.startMs >= g_lease.durationMs) {
        endLease("expired");
        return;
    }
    if (!state().transport) {
        return;
    }
    auto received = state().transport->poll();
    if (!received.has_value()) {
        return;
    }
    const uint8_t *data = received->payload.data();
    size_t len = received->payload.size();

    if (countPing(*received)) {
        if (!onLoRa() || g_ledger.allows(millis(), g_ledger.lastMs())) {
            uint32_t t0 = millis();
            replyToPing(*state().transport, *received);
            if (onLoRa()) {
                g_ledger.record(millis(), millis() - t0);
            }
        } else {
            g_lease.dutyDropped++;
        }
        return;
    }

    Token token;
    if (decodeToken(data, len, kReleaseKind, token) && token.campaignId == g_lease.campaignId &&
        token.pointId == g_lease.pointId) {
        sendToken(received->source, kReleaseAckKind, token.campaignId, token.pointId, kStatusOk);
        endLease("released");
        return;
    }

    // Only possible when the point's radio settings are close enough to home to hear it. The same
    // lease again means our ack was lost, so ack again; a different one means the node has moved
    // on, so this lease is over.
    LeaseRequest request;
    if (decodeLease(data, len, request)) {
        if (request.campaignId == g_lease.campaignId && request.pointId == g_lease.pointId) {
            sendToken(received->source, kLeaseAckKind, request.campaignId, request.pointId, kStatusOk);
        } else {
            endLease("superseded");
        }
    }
}

}   // namespace

void gatewaySetup() {
    esp_task_wdt_init(kWatchdogSeconds, true);
    esp_task_wdt_add(nullptr);

    Preferences prefs;
    prefs.begin("field", false);
    g_boot = prefs.getUInt("boots", 0) + 1;
    prefs.putUInt("boots", g_boot);
    prefs.end();

    // Format on first use only: the partition is blank on a freshly flashed board.
    g_logReady = LittleFS.begin(true);
    char line[48];
    snprintf(line, sizeof(line), "FLB,%lu,%d", static_cast<unsigned long>(g_boot),
             static_cast<int>(esp_reset_reason()));
    appendLog(line);

    enterHome();
}

void gatewayTick() {
    esp_task_wdt_reset();
    if (g_phase == Phase::Home) {
        tickHome();
    } else {
        tickLeased();
    }
}

void handleLogDump() {
    unsigned long count = 0;
    if (g_logReady) {
        File file = LittleFS.open(kLogPath, FILE_READ);
        if (file) {
            while (file.available()) {
                String line = file.readStringUntil('\n');
                line.trim();
                if (line.length() > 0) {
                    Serial.println(line);
                    count++;
                }
                esp_task_wdt_reset();
            }
            file.close();
        }
    }
    Serial.printf("LOG,end,%lu\n", count);
}

void handleLogClear() {
    if (g_logReady && LittleFS.exists(kLogPath)) {
        LittleFS.remove(kLogPath);
    }
    Serial.println("ACK");
}

unsigned long logBytes() {
    if (!g_logReady) {
        return 0;
    }
    File file = LittleFS.open(kLogPath, FILE_READ);
    if (!file) {
        return 0;
    }
    unsigned long size = file.size();
    file.close();
    return size;
}

}   // namespace bench::field

#endif   // BENCH_FIELD_GATEWAY
