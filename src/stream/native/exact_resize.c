/* Positive bilinear kernel with Pillow 9.0.1's 22-bit coefficients and
 * two separate uint8 rounding passes. No floating-point approximation flags.
 * Specification: Pillow/src/libImaging/Resample.c (9.0.1).
 * Coefficients are immutable; scratch belongs to each call (two-worker safe).
 */
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include <arm_neon.h>
#include <stdio.h>
#include <jpeglib.h>
#include <setjmp.h>
#include <time.h>
#include <string.h>
int pscnn_bgr_gray(const uint8_t *, uint8_t *, size_t);
typedef struct { int start, count; uint32_t *weight; } Kernel;
typedef struct { int width,height; Kernel x[64],y[64]; } Context;
void pscnn_resize_free(Context *ctx) {
    if (!ctx) return;
    for (int i=0;i<64;i++) { free(ctx->x[i].weight); free(ctx->y[i].weight); }
    free(ctx);
}
static int kernels(Kernel *out,int size) {
    double scale=(double)size/64, filter=scale>1?scale:1;
    double inv=1.0/filter;
    for (int i=0;i<64;i++) {
        double center=(i+0.5)*scale;
        int a=(int)(center-filter+0.5), b=(int)(center+filter+0.5);
        if(a<0) a=0;
        if(b>size) b=size;
        int n=b-a;
        double *tmp=calloc((size_t)n,sizeof(double));
        uint32_t *weights=calloc((size_t)n,sizeof(uint32_t));
        if(!tmp || !weights) {free(tmp); free(weights); return -1;}
        double sum=0;
        for(int j=0;j<n;j++) {double v=fabs((j+a-center+0.5)*inv); tmp[j]=v<1?1-v:0;sum+=tmp[j];}
        for(int j=0;j<n;j++) weights[j]=(uint32_t)(0.5+(tmp[j]/sum)*4194304.0);
        free(tmp);
        out[i].start=a;out[i].count=n;out[i].weight=weights;
    }
    return 0;
}
Context *pscnn_resize_new(int width,int height) {
    if(width<1 || height<1 || width>8192 || height>8192) return NULL;
    Context *ctx=calloc(1,sizeof(Context));
    if(!ctx) return NULL;
    ctx->width=width;ctx->height=height;
    if(kernels(ctx->x,width) || kernels(ctx->y,height)) {pscnn_resize_free(ctx);return NULL;}
    return ctx;
}
static uint8_t horizontal(const uint8_t *src,const Kernel *k) {
    int j=0;
    uint32x4_t sums=vdupq_n_u32(0);
    for(;j+8<=k->count;j+=8) {
        uint16x8_t p=vmovl_u8(vld1_u8(src+j));
        sums=vmlaq_u32(sums,vmovl_u16(vget_low_u16(p)),vld1q_u32(k->weight+j));
        sums=vmlaq_u32(sums,vmovl_u16(vget_high_u16(p)),vld1q_u32(k->weight+j+4));
    }
    uint32x2_t pair=vadd_u32(vget_low_u32(sums),vget_high_u32(sums));
    uint32_t sum=vget_lane_u32(vpadd_u32(pair,pair),0)+2097152u;
    for(;j<k->count;j++) sum+=(uint32_t)src[j]*k->weight[j];
    return (uint8_t)(sum>>22);
}
int pscnn_bgr_resize(Context *ctx,const uint8_t *bgr,const int8_t *table,int8_t *out) {
    if(!ctx || !bgr || !table || !out) return -1;
    uint8_t *gray=malloc((size_t)ctx->width);
    uint8_t *tmp=malloc((size_t)ctx->height*64);
    if(!gray || !tmp) {free(gray);free(tmp);return -2;}
    for(int y=0;y<ctx->height;y++) {
        pscnn_bgr_gray(bgr+(size_t)y*ctx->width*3,gray,(size_t)ctx->width);
        for(int x=0;x<64;x++) tmp[y*64+x]=horizontal(gray+ctx->x[x].start,&ctx->x[x]);
    }
    for(int y=0;y<64;y++) {
        const Kernel *k=&ctx->y[y];
        for(int x=0;x<64;x+=8) {
            uint32x4_t a=vdupq_n_u32(2097152u), b=a;
            for(int j=0;j<k->count;j++) {
                uint16x8_t p=vmovl_u8(vld1_u8(tmp+(k->start+j)*64+x));
                a=vmlaq_n_u32(a,vmovl_u16(vget_low_u16(p)),k->weight[j]);
                b=vmlaq_n_u32(b,vmovl_u16(vget_high_u16(p)),k->weight[j]);
            }
            uint8_t values[8];
            vst1_u8(values,vmovn_u16(vcombine_u16(vmovn_u32(vshrq_n_u32(a,22)),vmovn_u32(vshrq_n_u32(b,22)))));
            for(int j=0;j<8;j++) out[y*64+x+j]=table[values[j]];
        }
    }
    free(gray);free(tmp);return 0;
}

/* PS-only streaming JPEG -> exact Pillow grayscale/resize -> quantization LUT.
 * No decoder downscaling, fast IDCT, luma-only substitute, or PL changes.
 */
typedef struct {
    uint64_t header_ns,start_ns,decode_ns,gray_ns,horizontal_ns,finish_ns,
             vertical_ns,lut_ns,total_ns;
    uint32_t width,height,scanline_calls;
} PreReport;
typedef struct {struct jpeg_error_mgr base;jmp_buf jump;int warning;} Error;
static uint64_t tick(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000ull+(uint64_t)t.tv_nsec;
}
static void jpeg_failure(j_common_ptr p) {Error *e=(Error *)p->err;longjmp(e->jump,1);}
static void jpeg_message(j_common_ptr p,int level) {if(level<0)((Error *)p->err)->warning=1;}
int pscnn_jpeg_resize(Context *ctx,const uint8_t *data,size_t length,
                      const int8_t *table,int8_t *out,PreReport *report) {
    if(!ctx || !data || !length || !table || !out || !report)return -1;
    memset(report,0,sizeof(*report));
    uint64_t started=tick(),mark=started;
    struct jpeg_decompress_struct decoder;Error error;
    memset(&decoder,0,sizeof(decoder));memset(&error,0,sizeof(error));
    uint8_t *volatile rows=NULL,*volatile gray=NULL,*volatile tmp=NULL;
    volatile int created=0,code=-2;
    decoder.err=jpeg_std_error(&error.base);error.base.error_exit=jpeg_failure;error.base.emit_message=jpeg_message;
    if(setjmp(error.jump))goto cleanup;
    jpeg_create_decompress(&decoder);created=1;
    jpeg_mem_src(&decoder,data,length);
    if(jpeg_read_header(&decoder,TRUE)!=JPEG_HEADER_OK)goto cleanup;
    if((int)decoder.image_width!=ctx->width || (int)decoder.image_height!=ctx->height ||
        decoder.num_components>3) {code=-3;goto cleanup;}
    decoder.out_color_space=JCS_EXT_BGR;
    decoder.dct_method=JDCT_ISLOW;
    decoder.do_fancy_upsampling=TRUE;
    report->width=decoder.image_width;report->height=decoder.image_height;
    report->header_ns=tick()-mark;mark=tick();
    if(!jpeg_start_decompress(&decoder) || decoder.output_components!=3)goto cleanup;
    rows=malloc((size_t)ctx->width*3*8);gray=malloc((size_t)ctx->width*8);
    tmp=malloc((size_t)ctx->height*64);
    if(!rows || !gray || !tmp) {code=-4;goto cleanup;}
    report->start_ns=tick()-mark;
    while(decoder.output_scanline<decoder.output_height) {
        JSAMPROW pointers[8];
        for(int i=0;i<8;i++)pointers[i]=rows+(size_t)i*ctx->width*3;
        JDIMENSION y=decoder.output_scanline;
        mark=tick();JDIMENSION count=jpeg_read_scanlines(&decoder,pointers,8);
        report->decode_ns+=tick()-mark;report->scanline_calls++;
        if(!count)goto cleanup;
        mark=tick();
        pscnn_bgr_gray(rows,gray,(size_t)count*ctx->width);
        report->gray_ns+=tick()-mark;mark=tick();
        for(JDIMENSION r=0;r<count;r++)for(int x=0;x<64;x++)
            tmp[(y+r)*64+x]=horizontal(gray+(size_t)r*ctx->width+ctx->x[x].start,&ctx->x[x]);
        report->horizontal_ns+=tick()-mark;
    }
    mark=tick();
    if(!jpeg_finish_decompress(&decoder) || error.warning)goto cleanup;
    report->finish_ns=tick()-mark;mark=tick();
    uint8_t resized[4096];
    for(int y=0;y<64;y++) {
        const Kernel *k=&ctx->y[y];
        for(int x=0;x<64;x+=8) {
            uint32x4_t a=vdupq_n_u32(2097152u),b=a;
            for(int j=0;j<k->count;j++) {
                uint16x8_t p=vmovl_u8(vld1_u8(tmp+(k->start+j)*64+x));
                a=vmlaq_n_u32(a,vmovl_u16(vget_low_u16(p)),k->weight[j]);
                b=vmlaq_n_u32(b,vmovl_u16(vget_high_u16(p)),k->weight[j]);
            }
            vst1_u8(resized+y*64+x,vmovn_u16(vcombine_u16(vmovn_u32(vshrq_n_u32(a,22)),vmovn_u32(vshrq_n_u32(b,22)))));
        }
    }
    report->vertical_ns=tick()-mark;mark=tick();
    for(int i=0;i<4096;i++)out[i]=table[resized[i]];
    report->lut_ns=tick()-mark;code=0;
cleanup:
    if(created)jpeg_destroy_decompress(&decoder);
    free(rows);free(gray);free(tmp);
    report->total_ns=tick()-started;
    return code;
}
