/* One C call, full per-frame identity, PER-WORD write counts, complete input
 * comparison, stable status/error snapshots, reset/start/result acknowledgments.
 * No identity cache, no result cache, no approximations. The old Python session
 * remains available as a differential oracle. Physical mapping is owned by the
 * validated same-boot PinnedMMIO and global device lock.
 */
#define _POSIX_C_SOURCE 200809L
#include "native_frame.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if !defined(__arm__) && !defined(PSCNN_MOCK)
#error ARMv7 hardware backend required
#endif
#ifdef PSCNN_MOCK
extern uint32_t pscnn_mock_read(void *,uint32_t);
extern void pscnn_mock_write(void *,uint32_t,uint32_t);
#endif
struct FrameContext {void *base;uint64_t deadline;int owned,poison;uint32_t sleep_us;FrameReport *out;};
static uint64_t now_ns(void) {
    struct timespec t;
    if(clock_gettime(CLOCK_MONOTONIC,&t))return UINT64_MAX;
    return (uint64_t)t.tv_sec*1000000000ull+(uint64_t)t.tv_nsec;
}
static int expired(FrameContext *c) {return now_ns()>=c->deadline?E_TIMEOUT:0;}
static uint32_t rd(FrameContext *c,uint32_t off) {
    c->out->read_calls++;
#ifdef PSCNN_MOCK
    return pscnn_mock_read(c->base,off);
#else
    __asm__ volatile("dmb sy" ::: "memory");
    uint32_t v=*(volatile uint32_t *)((uint8_t *)c->base+off);
    __asm__ volatile("dmb sy" ::: "memory");
    return v;
#endif
}
static void wr(FrameContext *c,uint32_t off,uint32_t value) {
    c->out->write_calls++;
#ifdef PSCNN_MOCK
    pscnn_mock_write(c->base,off,value);
#else
    __asm__ volatile("dmb sy" ::: "memory");
    *(volatile uint32_t *)((uint8_t *)c->base+off)=value;
    __asm__ volatile("dsb sy" ::: "memory");
#endif
}
static void input_word(FrameContext *c,uint32_t off,uint32_t value) {
    c->out->write_calls++;
#ifdef PSCNN_MOCK
    pscnn_mock_write(c->base,off,value);
#else
    __asm__ volatile("dmb sy" ::: "memory");
    *(volatile uint32_t *)((uint8_t *)c->base+off)=value;
#endif
}
static int pause_poll(FrameContext *c) {
    c->out->poll_count++;
    if(expired(c))return E_TIMEOUT;
    if(c->sleep_us) {
        struct timespec t={0,(long)c->sleep_us*1000};
        nanosleep(&t,NULL);
    }
    return expired(c);
}
static int status(FrameContext *c,uint32_t *flags,uint32_t *error) {
    for(;;) {
        if(expired(c))return E_TIMEOUT;
        uint32_t a=rd(c,12),e=rd(c,16),b=rd(c,12);
        if(a!=b) {int r=pause_poll(c);if(r)return r;continue;}
        if((a&~31u) || (!!(a&16)!=!!e) || ((a&1)&&(a&6)) || (e&&(a&2)))return E_STATUS;
        *flags=a;*error=e;return expired(c);
    }
}
static int idle(FrameContext *c) {
    uint32_t s,e;int r=status(c,&s,&e);
    if(r)return r;
    return (e || (s&1) || !(s&2) || (s&4))?E_STATUS:0;
}
static int identity(FrameContext *c) {
    static const uint32_t quant[8]={0x346bafa3,0x1f56bd5a,0x28bff0d8,0xee7d8cd8,0x50e5e2da,0x7f56e536,0x037e46d0,0x33dc4225};
    static const uint32_t commit[5]={0x619308c7,0x7fd9af7d,0x03bc89c4,0x94a33dda,0x5d07386d};
    if(expired(c))return E_TIMEOUT;
    if(rd(c,0)!=0x53434e4e || rd(c,4)!=0x10000 || rd(c,44)!=4096)return E_IDENTITY;
    for(int i=0;i<8;i++)if(rd(c,48+4*i)!=quant[i])return E_IDENTITY;
    for(int i=0;i<5;i++)if(rd(c,80+4*i)!=commit[i])return E_IDENTITY;
    return expired(c);
}
static int reset(FrameContext *c) {
    uint32_t previous=rd(c,24),expected=previous+1u;
    c->out->reset_requests++;wr(c,8,1);
    for(;;) {
        if(expired(c))return E_TIMEOUT;
        uint32_t seq=rd(c,24);
        if(seq==expected) {int r=idle(c);if(r)return r;return rd(c,20)==0?expired(c):E_COUNT;}
        if(seq!=previous)return E_RESET;
        int r=pause_poll(c);if(r)return r;
    }
}
static int recover_owned(FrameContext *c) {
    if(!c->owned)return 0;
    uint64_t old=c->deadline;c->deadline=now_ns()+250000000ull;
    int r=identity(c);
    if(!r)r=reset(c);
    c->deadline=old;
    c->poison=r!=0;
    if(!r)c->owned=0;
    return r;
}
FrameContext *pscnn_frame_new(void *base) {
    if(!base || ((uintptr_t)base&3))return NULL;
    FrameContext *c=calloc(1,sizeof(*c));if(c)c->base=base;return c;
}
uint32_t pscnn_frame_report_size(void) {return (uint32_t)sizeof(FrameReport);}
void pscnn_frame_free(FrameContext *c) {free(c);}
int pscnn_frame_infer(FrameContext *c,const uint8_t *input,uint32_t bytes,uint64_t timeout_ns,uint32_t sleep_us,uint32_t ack_words,FrameReport *out) {
    if(!out)return E_ARGUMENT;
    memset(out,0,sizeof(*out));out->code=E_ARGUMENT;
    if(!c || !input || bytes!=4096 || timeout_ns<1000 || timeout_ns>60000000000ull || sleep_us>1000 ||
       (ack_words!=1 && ack_words!=16 && ack_words!=64 && ack_words!=1024))return E_ARGUMENT;
    if(c->poison) {out->code=E_POISON;return E_POISON;}
    for(uint32_t i=0;i<4096;i++)if(input[i]>127)return E_ARGUMENT;
    uint64_t started=now_ns(),mark=started;
    c->out=out;c->sleep_us=sleep_us;c->deadline=started+timeout_ns;
    int r=0;uint32_t s=0,e=0;
#define CHECK(op) do{r=(op);if(r)goto fail;}while(0)
    out->phase=P_IDENTITY;CHECK(identity(c));out->identity_ns=now_ns()-mark;
    out->phase=P_INITIAL;CHECK(status(c,&s,&e));
    if(s&1) {r=E_BUSY;goto fail;}
    if(e) {r=E_STATUS;goto fail;}
    c->owned=1;
    out->phase=P_RESET;mark=now_ns();CHECK(reset(c));out->reset_ns=now_ns()-mark;
    out->phase=P_WRITE;mark=now_ns();CHECK(idle(c));
    if(rd(c,20)!=0) {r=E_COUNT;goto fail;}
    for(uint32_t i=0;i<1024;i++) {
        if(!(i&63u))CHECK(expired(c));
        const uint8_t *p=input+4*i;
        uint32_t word=(uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
        input_word(c,4096+4*i,word);out->bytes_written+=4;
        if(!((i+1)&(ack_words-1u))) {
            out->count_checks++;
            if(rd(c,20)!=(i+1)*4) {r=E_COUNT;goto fail;}
        }
    }
#ifndef PSCNN_MOCK
    __asm__ volatile("dsb sy" ::: "memory");
#endif
    CHECK(expired(c));CHECK(idle(c));if(rd(c,20)!=4096) {r=E_COUNT;goto fail;}
    out->write_ns=now_ns()-mark;
    out->phase=P_READBACK;mark=now_ns();CHECK(idle(c));
    if(rd(c,20)!=4096) {r=E_COUNT;goto fail;}
    for(uint32_t i=0;i<1024;i++) {
        if(!(i&63u))CHECK(expired(c));
        uint32_t word=rd(c,4096+4*i);out->bytes_read+=4;
        const uint8_t *p=input+4*i;
        if(word!=((uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24))) {r=E_READBACK;goto fail;}
    }
    CHECK(expired(c));CHECK(idle(c));if(rd(c,20)!=4096) {r=E_COUNT;goto fail;}
    out->readback_ns=now_ns()-mark;
    out->phase=P_START;mark=now_ns();CHECK(status(c,&s,&e));
    if(e || (s&1) || !(s&2) || (s&4) || !(s&8) || rd(c,20)!=4096) {r=E_STATUS;goto fail;}
    uint32_t previous=rd(c,28),expected=previous+1u;
    out->start_requests++;wr(c,8,2);
    for(;;) {
        CHECK(expired(c));uint32_t seq=rd(c,28);CHECK(status(c,&s,&e));
        if(e) {r=E_STATUS;goto fail;}
        if(seq==expected) {if(rd(c,20)!=0) {r=E_COUNT;goto fail;}break;}
        if(seq!=previous) {r=E_START;goto fail;}
        CHECK(pause_poll(c));
    }
    out->phase=P_WAIT;
    while(!(s&4)) {CHECK(pause_poll(c));CHECK(status(c,&s,&e));if(e) {r=E_STATUS;goto fail;}}
    out->start_wait_ns=now_ns()-mark;
    out->phase=P_OUTPUT;mark=now_ns();CHECK(status(c,&s,&e));
    if(e || (s&1) || !(s&4)) {r=E_RESULT;goto fail;}
    uint32_t before=rd(c,40);
    if(before!=expected) {r=E_RESULT;goto fail;}
    uint32_t collision=rd(c,32),steering=rd(c,36),after=rd(c,40);
    CHECK(status(c,&s,&e));
    if(before!=after || after!=expected || e || (s&1) || !(s&4)) {r=E_RESULT;goto fail;}
    out->phase=P_FINAL;CHECK(status(c,&s,&e));
    if(e || (s&1) || !(s&4)) {r=E_RESULT;goto fail;}
    CHECK(expired(c));
    out->collision=collision;out->steering=steering;out->result_seq=expected;
    out->output_ns=now_ns()-mark;out->code=0;out->total_ns=now_ns()-started;c->owned=0;return 0;
fail:
    out->code=r;
    out->recovery_code=recover_owned(c);
    out->total_ns=now_ns()-started;return r;
#undef CHECK
}
int pscnn_frame_recover(FrameContext *c,uint64_t timeout_ns,FrameReport *out) {
    if(!c || !out || timeout_ns<1000 || timeout_ns>60000000000ull)return E_ARGUMENT;
    memset(out,0,sizeof(*out));c->out=out;c->deadline=now_ns()+timeout_ns;out->phase=P_RECOVERY;
    int r=identity(c);uint32_t s,e;
    if(!r && !c->owned) {r=status(c,&s,&e);if(!r && (s&1))r=E_BUSY;}
    if(!r) {c->owned=1;r=reset(c);}
    if(!r) {c->owned=0;c->poison=0;}
    else if(c->owned)c->poison=1;
    out->code=r;return r;
}
