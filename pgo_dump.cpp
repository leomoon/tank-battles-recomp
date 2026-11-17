// Compiled only into a profile-instrumented title build (TB_TITLE_PGO=generate).
//
// The player can leave through paths that skip the profile runtime's exit hook
// (window close tears the process down after its own shutdown sequence), so a
// background thread writes the counters every few seconds; the last write
// before exit carries nearly the whole training run. LLVM_PROFILE_FILE names
// the output, as usual for clang's instrumentation.
#include <chrono>
#include <cstdio>
#include <thread>

extern "C" int __llvm_profile_write_file(void);

namespace {
struct ProfileDumper {
    ProfileDumper() {
        std::thread([] {
            for (;;) {
                std::this_thread::sleep_for(std::chrono::seconds(5));
                if (__llvm_profile_write_file() != 0) std::fprintf(stderr, "[pgo] profile write failed\n");
            }
        }).detach();
        std::fprintf(stderr, "[pgo] instrumented title build: writing the profile every 5 s\n");
    }
} dumper;
}
