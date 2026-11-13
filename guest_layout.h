#pragma once
#include <stdint.h>
// RPCS3's PS3 memory map reserves 0xD0000000..0xDFFFFFFF for stacks.
// NPUB30154 classifies stack transfers by the address's high nibble. A stack
// in segment zero makes a null synchronization command take the CPU-copy path.
static constexpr uint32_t TB_MAIN_STACK_TOP = 0xD0100000u;
static constexpr uint32_t TB_MAIN_STACK_SIZE = 0x00100000u;
