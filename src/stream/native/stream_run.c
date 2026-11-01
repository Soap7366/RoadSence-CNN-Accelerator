/* Binary stop-and-wait stream over the EXISTING SSH stdin/stdout pipes.
 * Every frame invokes native_frame.c with all chosen checks and full readback.
 * SHA256, token, frame ID, nanosecond PTS, lengths and real PL outputs verified.
 * Python retains setup/locking and materializes reports after the run.
 */
#define _POSIX_C_SOURCE 200809L
#include "native_frame.h"
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <poll.h>
#include <time.h>
#include <unistd.h>
#include <openssl/sha.h>
typedef struct {uint32_t frame_id;uint64_t pts_ns;uint8_t sha[32];int32_t collision,steering;} StreamMeta;
typedef struct {FrameReport frame;uint64_t control_ns,request_ns;} StreamReport;
uint32_t pscnn_stream_meta_size(void) {return sizeof(StreamMeta);}
uint32_t pscnn_stream_report_size(void) {return sizeof(StreamReport);}
int pscnn_stream_warmup(void) {uint8_t digest[32];return SHA256((const uint8_t *)"",0,digest)?0:-1;}
static uint64_t tick(void) {struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return UINT64_MAX;return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;}
static uint32_t u32(const uint8_t *p) {return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static uint64_t u64(const uint8_t *p) {return u32(p)|((uint64_t)u32(p+4)<<32);}
static void p32(uint8_t *p,uint32_t n) {for(int i=0;i<4;i++)p[i]=(uint8_t)(n>>(8*i));}
static void p64(uint8_t *p,uint64_t n) {p32(p,(uint32_t)n);p32(p+4,(uint32_t)(n>>32));}
static int io_exact(int fd,uint8_t *p,uint32_t n,int writing) {
    uint64_t deadline=tick()+15000000000ull;
    while(n) {
        uint64_t now=tick();if(now>=deadline)return -20;
        int ms=(int)((deadline-now+999999ull)/1000000ull);
        struct pollfd f={fd,writing?POLLOUT:POLLIN,0};
        int ready=poll(&f,1,ms);
        if(ready<0 && errno==EINTR)continue;
        if(ready<=0)return -20;
        ssize_t got=writing?write(fd,p,n):read(fd,p,n);
        if(got<0 && errno==EINTR)continue;
        if(got<=0)return -21;
        p+=got;n-=(uint32_t)got;
    }
    return 0;
}
int pscnn_stream_run(FrameContext *ctx,int input_fd,int output_fd,uint32_t token,uint32_t live,
                     const StreamMeta *meta,const uint8_t *stored,uint32_t n,uint64_t timeout_ns,
                     uint32_t sleep_us,uint32_t ack_words,StreamReport *reports,uint32_t *done) {
    if(!ctx || input_fd<0 || output_fd<0 || live>1 || !meta || !n || n>30000 || (!live&&!stored) || !reports || !done)return -1;
    *done=0;
    for(uint32_t i=0;i<n;i++) {
        uint8_t header[56],payload[4096],digest[32],response[116]={0};
        int r=io_exact(input_fd,header,sizeof(header),0);if(r)return r;
        const StreamMeta *m=meta+i;
        if(memcmp(header,"SCNq",4)||u32(header+4)!=token||u32(header+8)!=m->frame_id||u64(header+12)!=m->pts_ns||
           u32(header+20)!=(live?4096u:0u)||memcmp(header+24,m->sha,32))return -22;
        const uint8_t *data=live?payload:stored+(size_t)i*4096;
        if(live) {r=io_exact(input_fd,payload,4096,0);if(r)return r;data=payload;}
        uint64_t received=tick();
        if(!SHA256(data,4096,digest)||memcmp(digest,m->sha,32))return -23;
        StreamReport *out=reports+i;uint64_t control=tick();
        r=pscnn_frame_infer(ctx,data,4096,timeout_ns,sleep_us,ack_words,&out->frame);
        out->control_ns=tick()-control;
        if(r)return r;
        if(out->frame.bytes_read!=4096 || out->frame.collision!=(uint32_t)m->collision || out->frame.steering!=(uint32_t)m->steering)return -24;
        out->request_ns=tick()-received;
        memcpy(response,"SCNr",4);p32(response+4,token);p32(response+8,m->frame_id);p64(response+12,m->pts_ns);
        memcpy(response+20,digest,32);p32(response+52,out->frame.collision);p32(response+56,out->frame.steering);
        p32(response+60,out->frame.result_seq);p32(response+64,ack_words==1?15:13);
        p64(response+68,out->control_ns);p64(response+76,out->request_ns);p64(response+84,out->frame.start_wait_ns);
        p64(response+92,out->frame.write_ns);p64(response+100,out->frame.readback_ns);p64(response+108,out->frame.output_ns);
        r=io_exact(output_fd,response,sizeof(response),1);if(r)return r;
        *done=i+1;
    }
    return 0;
}
