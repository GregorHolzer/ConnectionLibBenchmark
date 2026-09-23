// ConnectionLibBenchmark device firmware.
//
// One image, all three backends, selected at run time by `CFG` commands arriving over USB serial.
// A 10h campaign visits several hundred configuration points and a rebuild plus two flashes costs
// ~80s, so build-time configuration would spend the campaign flashing rather than measuring. This
// needed no changes to ConnectionLib: constructing a fresh Transport re-runs setup(), and all
// three backends tear down cleanly.
//
// Protocol (line-based, 115200 baud):
//   PING                     -> PONG,<role>
//   INFO                     -> INFO,<key>,<value> ...   build/environment facts for the audit
//   CFG tech=lora sf=9 ...   -> ACK  |  ERR,<reason>      (also emits the PPH run header)
//   RUN                      -> PPD,... PPS,... RDY       (gateway only)
//   IDLE <ms>                -> PPI,... RDY               standing-cost hold, no exchanges
//   RFCHECK [RFO]            -> RF,... RDY                433 MHz noise floor + PA diagnostic
//   RESET                    -> ACK                       clears node-side counters
//   LEASE ... / RELEASE      -> see FieldNode.hpp          field node only
//   LOG / LOGCLEAR           -> see FieldGateway.hpp       field gateway only
//
// The host configures the node first and the gateway second, and both must ACK before RUN.
// In field mode (docs/field-campaign.md) there is no host at the gateway: the field node reaches it
// over the air with LEASE, and the field gateway's USB port is only used to read its log back.
//
// This file is only the dispatcher and the two Arduino entry points. The verbs live in
// BenchCommands.cpp, the diagnostic in RfCheck.cpp, and what the board reports about itself in
// BenchReporting.cpp.

#include <cstddef>
#include <cstring>

#include <Arduino.h>

#include "BenchCommands.hpp"
#include "BenchDisplay.hpp"
#include "BenchPower.hpp"
#include "BenchReporting.hpp"
#include "BenchRunner.hpp"
#include "BenchState.hpp"
#include "FieldGateway.hpp"
#include "FieldNode.hpp"
#include "RfCheck.hpp"

using bench::Tech;

namespace {

void handleLine(char *line) {
    while (*line == ' ' || *line == '\t') {
        line++;
    }
    if (*line == '\0') {
        return;
    }

#if defined(BENCH_FIELD_GATEWAY)
    // Configured over the air only. A CFG arriving here would bypass the lease and leave the
    // gateway somewhere the node doesn't know about.
    if (strcmp(line, "PING") == 0) {
        Serial.printf("PONG,%s\n", bench::role());
    } else if (strcmp(line, "INFO") == 0) {
        bench::emitInfo();
    } else if (strcmp(line, "LOG") == 0) {
        bench::field::handleLogDump();
    } else if (strcmp(line, "LOGCLEAR") == 0) {
        bench::field::handleLogClear();
    } else {
        Serial.println("ERR,the field gateway takes only PING, INFO, LOG and LOGCLEAR");
    }
    return;
#endif

#if defined(BENCH_FIELD_NODE)
    if (strncmp(line, "LEASE", 5) == 0) {
        bench::field::handleLease(line + 5);
        return;
    }
    if (strcmp(line, "RELEASE") == 0) {
        bench::field::handleRelease();
        return;
    }
#endif

    if (strncmp(line, "CFG", 3) == 0) {
        bench::handleConfigure(line + 3);
    } else if (strncmp(line, "IDLE", 4) == 0) {
        bench::handleIdle(line + 4);
    } else if (strcmp(line, "RUN") == 0) {
        bench::handleRun();
    } else if (strcmp(line, "PING") == 0) {
        Serial.printf("PONG,%s\n", bench::role());
    } else if (strcmp(line, "RFCHECK") == 0) {
        bench::handleRfCheck(false);
    } else if (strcmp(line, "RFCHECK RFO") == 0) {
        bench::handleRfCheck(true);
    } else if (strcmp(line, "INFO") == 0) {
        bench::emitInfo();
    } else if (strcmp(line, "RESET") == 0) {
        bench::resetNodeCounters();
        Serial.println("ACK");
    } else {
        Serial.println("ERR,unknown command");
    }
}

}   // namespace

void setup() {
    Serial.begin(115200);
    delay(500);

    bench::setupPower();
    bench::setRadioIdleState(Tech::None);   // start with WiFi down; ESP-NOW brings it up when selected

    bench::setupDisplay();
    bench::showStatus("idle");

#if defined(BENCH_FIELD_GATEWAY)
    bench::field::gatewaySetup();
#endif

    Serial.printf("BENCH,%s,ready\n", bench::role());
}

void loop() {
    static char buffer[256];
    static size_t length = 0;

    while (Serial.available()) {
        char c = static_cast<char>(Serial.read());
        if (c == '\n' || c == '\r') {
            if (length > 0) {
                buffer[length] = '\0';
                handleLine(buffer);
                length = 0;
            }
        } else if (length < sizeof(buffer) - 1) {
            buffer[length++] = c;
        }
    }

#if defined(BENCH_FIELD_GATEWAY)
    bench::field::gatewayTick();
#elif defined(BENCH_ROLE_NODE)
    // The node has no RUN step: once configured it answers whatever arrives, for as long as the
    // gateway keeps sending. Serial is still serviced above, so a new CFG lands between polls.
    bench::State &s = bench::state();
    if (s.configured && s.transport) {
        bench::nodeTick(*s.transport);
    }
#endif
}
