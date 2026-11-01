/* Build on the board: gcc -O2 -shared -fPIC -Wall -Wextra -Werror
 * -o native/libpscnn_mmio.so native/ordered_mmio.c
 * Uses the already validated PYNQ mapping, no extra /dev/mem mapping.
 * A stalled AXI store cannot be interrupted by a Python deadline.
 */
#include <stdint.h>
#if !defined(__arm__)
#error "This implementation requires ARMv7 ordered device stores"
#endif
int pscnn_write_control(void *mapping,uint32_t value) {
    if(!mapping || (value!=1 && value!=2))return -1;
    __asm__ volatile("dmb sy" ::: "memory");
    *(volatile uint32_t *)((uint8_t *)mapping+8)=value;
    __asm__ volatile("dsb sy" ::: "memory");
    return 0;
}
int pscnn_read_words(void *mapping, const uint32_t *offsets, uint32_t *out, uint32_t count) {
    if (!mapping || !offsets || !out || !count || count>32) return -1;
    for (uint32_t i=0; i<count; ++i)
        if (offsets[i]>0x60 || offsets[i]%4) return -1;
    for (uint32_t i=0; i<count; ++i) {
        __asm__ volatile("dmb sy" ::: "memory");
        out[i]=*(volatile uint32_t *)((uint8_t *)mapping+offsets[i]);
        __asm__ volatile("dmb sy" ::: "memory");
    }
    return 0;
}
int pscnn_write_words(void *mapping, uint32_t offset, const void *source, uint32_t bytes) {
    if (!mapping || !source || offset < 4096 || offset > 8192 ||
        offset % 4 || !bytes || bytes % 4 || bytes > 8192-offset) return -1;
    volatile uint32_t *destination = (volatile uint32_t *)((uint8_t *)mapping + offset);
    const uint8_t *input = source;
    for (uint32_t i=0; i<bytes/4; ++i) {
        uint32_t j=i*4;
        uint32_t word=(uint32_t)input[j] | ((uint32_t)input[j+1]<<8) |
                      ((uint32_t)input[j+2]<<16) | ((uint32_t)input[j+3]<<24);
        __asm__ volatile("dmb sy" ::: "memory");
        destination[i]=word;
    }
    __asm__ volatile("dsb sy" ::: "memory");
    return 0;
}
int pscnn_read_input(void *mapping, uint32_t offset, void *output, uint32_t bytes) {
    if (!mapping || !output || offset < 4096 || offset > 8192 || offset % 4 ||
        !bytes || bytes % 4 || bytes > 8192-offset) return -1;
    volatile uint32_t *source=(volatile uint32_t *)((uint8_t *)mapping+offset);
    uint8_t *dst=output;
    for(uint32_t i=0;i<bytes/4;i++) {
        __asm__ volatile("dmb sy" ::: "memory");
        uint32_t word=source[i];
        __asm__ volatile("dmb sy" ::: "memory");
        dst[4*i]=word;dst[4*i+1]=word>>8;dst[4*i+2]=word>>16;dst[4*i+3]=word>>24;
    }
    return 0;
}
