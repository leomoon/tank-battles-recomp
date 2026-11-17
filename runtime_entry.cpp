#include "host_utf8_args.h"
int tb_player_main(int,char**);
#ifdef _WIN32
TB_UTF8_ENTRY(tb_player_main)
#else
int main(int argc,char** argv) {return tb_player_main(argc,argv);}
#endif
