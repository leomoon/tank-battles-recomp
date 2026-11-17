#pragma once
// Original game-free ABI fixture. Contains no generated or guest game code.
#include <cstddef>
#include <cstdint>
struct ppu_context {
    uint64_t gpr[32]{};
    uint64_t thread_id = 0;
};
extern std::size_t ppu_vm_size;
uint8_t vm_read8(uint64_t address);
void vm_write8(uint64_t address, uint8_t value);
