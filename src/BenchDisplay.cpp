/** @brief OLED status line, or nothing at all when the build has no display. */

#include "BenchDisplay.hpp"

#include <cstdio>

#include "BenchReporting.hpp"
#include "BenchState.hpp"

#if defined(BENCH_HAS_DISPLAY)
#include <Wire.h>
#include <U8g2lib.h>

namespace {
U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE);
}   // namespace
#endif

namespace bench {

#if defined(BENCH_HAS_DISPLAY)

void setupDisplay() {
    Wire.begin(21, 22);
    display.begin();
}

void showStatus(const char *line) {
    State &s = state();
    char detail[32];
    snprintf(detail, sizeof(detail), "%s %uB", techName(s.config.tech), s.config.payloadBytes);
    char points[32];
    snprintf(points, sizeof(points), "points: %lu", static_cast<unsigned long>(s.pointsRun));

    display.clearBuffer();
    display.setFont(u8g2_font_6x10_tf);
    display.drawStr(0, 10, "ConnectionLib bench");
    display.drawStr(0, 24, role());
    display.drawStr(0, 38, detail);
    display.drawStr(0, 52, line);
    display.drawStr(70, 24, points);
    display.sendBuffer();
}

#else

void setupDisplay() {}
void showStatus(const char *) {}

#endif

}   // namespace bench
