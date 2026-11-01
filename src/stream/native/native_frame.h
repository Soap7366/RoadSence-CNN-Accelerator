#ifndef PSCNN_NATIVE_FRAME_H
#define PSCNN_NATIVE_FRAME_H
#include <stdint.h>
enum { P_IDENTITY=1,P_INITIAL,P_RESET,P_WRITE,P_READBACK,P_START,P_WAIT,P_OUTPUT,P_FINAL,P_RECOVERY };
enum { E_ARGUMENT=-1,E_IDENTITY=-2,E_BUSY=-3,E_STATUS=-4,E_RESET=-5,E_COUNT=-6,E_READBACK=-7,E_START=-8,E_RESULT=-9,E_TIMEOUT=-10,E_POISON=-11 };
typedef struct {
    int32_t code,phase,recovery_code;
    uint32_t bytes_written,bytes_read,result_seq,collision,steering;
    uint32_t reset_requests,start_requests,read_calls,write_calls,poll_count,count_checks;
    uint64_t identity_ns,reset_ns,write_ns,readback_ns,start_wait_ns,output_ns,total_ns;
} FrameReport;
typedef struct FrameContext FrameContext;
FrameContext *pscnn_frame_new(void *base);
uint32_t pscnn_frame_report_size(void);
void pscnn_frame_free(FrameContext *ctx);
int pscnn_frame_infer(FrameContext *ctx,const uint8_t *input,uint32_t bytes,uint64_t timeout_ns,uint32_t sleep_us,uint32_t ack_words,FrameReport *out);
int pscnn_frame_recover(FrameContext *ctx,uint64_t timeout_ns,FrameReport *out);
#endif
