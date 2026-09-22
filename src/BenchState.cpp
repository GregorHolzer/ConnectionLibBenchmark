/** @brief Definitions for the shared bench state and the build-time role name. */

#include "BenchState.hpp"

namespace bench {

State &state() {
    static State instance;
    return instance;
}

const char *role() {
#if defined(BENCH_ROLE_GATEWAY)
    return "gateway";
#else
    return "node";
#endif
}

}   // namespace bench
