/**
 * @brief The optional OLED status line.
 *
 * A no-op when BENCH_HAS_DISPLAY is not defined, so every caller can write showStatus()
 * unconditionally instead of guarding each call site.
 */

#pragma once

namespace bench {

/** @brief Redraw the status screen with `line` as its last row. */
void showStatus(const char *line);

/** @brief Bring the panel up, if this build has one. */
void setupDisplay();

}   // namespace bench
