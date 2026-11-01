/* Algebraically exact: G + round((19595*(R-G)+7471*(B-G))/65536).
 * This removes one multiply per pixel. Signed differences require 16 bits;
 * products and their sum fit int32. NEON rounding matches +32768 then >>16.
 */
#include <arm_neon.h>
#include <stdint.h>
#include <stddef.h>
static uint8x8_t gray8(uint8x8_t b,uint8x8_t g,uint8x8_t r) {
    int16x8_t rg=vreinterpretq_s16_u16(vsubl_u8(r,g));
    int16x8_t bg=vreinterpretq_s16_u16(vsubl_u8(b,g));
    int32x4_t lo=vmull_n_s16(vget_low_s16(rg),19595);
    int32x4_t hi=vmull_n_s16(vget_high_s16(rg),19595);
    lo=vmlal_n_s16(lo,vget_low_s16(bg),7471);
    hi=vmlal_n_s16(hi,vget_high_s16(bg),7471);
    int16x8_t delta=vcombine_s16(vrshrn_n_s32(lo,16),vrshrn_n_s32(hi,16));
    int16x8_t out=vaddq_s16(vreinterpretq_s16_u16(vmovl_u8(g)),delta);
    return vmovn_u16(vreinterpretq_u16_s16(out));
}
int pscnn_bgr_gray(const uint8_t *src,uint8_t *dst,size_t pixels) {
    if(!src || !dst || !pixels)return -1;
    size_t i=0;
    for(;i+16<=pixels;i+=16) {
        uint8x16x3_t p=vld3q_u8(src+3*i);
        vst1q_u8(dst+i,vcombine_u8(gray8(vget_low_u8(p.val[0]),vget_low_u8(p.val[1]),vget_low_u8(p.val[2])),gray8(vget_high_u8(p.val[0]),vget_high_u8(p.val[1]),vget_high_u8(p.val[2]))));
    }
    for(;i<pixels;i++)dst[i]=(19595u*src[3*i+2]+38470u*src[3*i+1]+7471u*src[3*i]+32768u)>>16;
    return 0;
}
