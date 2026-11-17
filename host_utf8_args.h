#pragma once
// The host filesystem bridge accepts UTF-8, including paths from Windows argv.
#ifdef _WIN32
#include <windows.h>
#include <stdlib.h>
static int tb_host_utf8_entry(int count,wchar_t** wide,int (*run)(int,char**)) {
    char** args=(char**)calloc((size_t)count+1,sizeof(char*));
    if(!args)return 2;
    int result=2,ready=1;
    for(int i=0;i<count;++i) {
        int size=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,NULL,0,NULL,NULL);
        args[i]=size?(char*)malloc((size_t)size):NULL;
        if(!args[i] || !WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,args[i],size,NULL,NULL)){ready=0;break;}
    }
    if(ready)result=run(count,args);
    for(int i=0;i<count;++i)free(args[i]);
    free(args);return result;
}
#define TB_UTF8_ENTRY(function) int wmain(int count,wchar_t** args){return tb_host_utf8_entry(count,args,function);}
#endif
