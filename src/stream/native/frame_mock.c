/* Software bridge for fault injection. No physical addresses or /dev/mem.
 * Used by the same native_frame.c with PSCNN_MOCK to exercise its real logic.
 */
#include <stdint.h>
#include <stdlib.h>
typedef struct {uint32_t reg[2048],fault,resets,starts,status_reads;} Mock;
void *pscnn_mock_new(void) {
    Mock *m=calloc(1,sizeof(*m));if(!m)return NULL;
    const uint32_t q[8]={0x346bafa3,0x1f56bd5a,0x28bff0d8,0xee7d8cd8,0x50e5e2da,0x7f56e536,0x037e46d0,0x33dc4225};
    const uint32_t c[5]={0x619308c7,0x7fd9af7d,0x03bc89c4,0x94a33dda,0x5d07386d};
    m->reg[0]=0x53434e4e;m->reg[1]=0x10000;m->reg[3]=2;m->reg[11]=4096;
    for(int i=0;i<8;i++)m->reg[12+i]=q[i];
    for(int i=0;i<5;i++)m->reg[20+i]=c[i];
    return m;
}
void pscnn_mock_free(void *p) {free(p);}
void pscnn_mock_fault(void *p,uint32_t fault) {((Mock *)p)->fault=fault;}
uint32_t pscnn_mock_counts(void *p,uint32_t which) {Mock *m=p;return which?m->starts:m->resets;}
void pscnn_mock_wrap(void *p) {Mock *m=p;m->reg[6]=0xffffffff;m->reg[7]=0xffffffff;}
uint32_t pscnn_mock_read(void *p,uint32_t off) {
    Mock *m=p;uint32_t v=m->reg[off/4];
    if(m->fault==1 && off==0)return 0;
    if(m->fault==2 && off==48)return v^1;
    if(m->fault==3 && off==80)return v^1;
    if(off==12) {
        m->status_reads++;
        if(m->fault==4 && m->starts==0)return 1;
        if(m->fault==16)return v|32;
        if(m->fault==17)return v|16;
        if(m->fault==18)return 18;
        if(m->fault==19)return 5;
        if(m->fault==26)return 16;
        if(m->fault==24 && m->status_reads==1)return v|1;
    }
    if(off==16 && m->fault==18)return 1;
    if(off==16 && m->fault==26)return 1;
    if((m->fault==9 && off==4096)||(m->fault==10 && off==8188)||(m->fault==23 && off==6144))return v^1;
    if(m->fault==14 && off==36)m->reg[10]++;
    if(m->fault==15 && off==32)m->reg[3]=2;
    return v;
}
void pscnn_mock_write(void *p,uint32_t off,uint32_t value) {
    Mock *m=p;
    if(off==8 && value==1) {
        m->resets++;
        if(m->fault==22)return;
        m->reg[6]+=m->fault==5?2:1;
        m->reg[3]=2;m->reg[4]=0;m->reg[5]=m->fault==6?4:0;
    } else if(off==8 && value==2) {
        m->starts++;m->reg[7]+=m->fault==11?2:1;
        m->reg[10]=m->reg[7]+(m->fault==13?1:0);
        m->reg[5]=m->fault==12?4096:0;
        m->reg[3]=m->fault==21?1:6;
        m->reg[8]=0x80000000;m->reg[9]=0x7fffffff;
    } else if(off>=4096 && off<8192) {
        m->reg[off/4]=value;
        m->reg[5]+=m->fault==7?0:4;
        if(m->fault==8)m->reg[5]=4097;
        if(m->reg[5]==4096)m->reg[3]|=8;
        if(m->fault==20 && off==8188) {m->reg[3]=16;m->reg[4]=1;}
    }
}
