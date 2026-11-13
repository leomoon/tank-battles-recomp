#include "spu_context.h"
#include "spu_interp.h"
void tb_spu_execution_probe(unsigned microstep){
 static unsigned char ls[SPU_LS_SIZE];
 spu_context context={0};context.ls=ls;context.image_id=90001;
 if(microstep){context.pc=0x100;spu_indirect_branch(&context);}
 else spu_interp_run_until(&context,0x100,0);
}
