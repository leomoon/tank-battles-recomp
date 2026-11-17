/* UTF-8 host paths, process-death-safe locks and same-volume publication. */
#pragma once
#ifndef _CRT_RAND_S
#define _CRT_RAND_S
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#include <wchar.h>
static wchar_t* tb_cache_wide(const char* path) {
    int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path,-1,NULL,0);
    if(!n)return NULL;
    wchar_t* wide=(wchar_t*)malloc((size_t)n*sizeof(wchar_t));
    if(wide && !MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path,-1,wide,n)){free(wide);return NULL;}
    return wide;
}
static FILE* tb_cache_fopen(const char* path,const char* mode) {
    wchar_t* wide=tb_cache_wide(path);if(!wide)return NULL;
    FILE* result=_wfopen(wide,mode[0]=='r'?L"rb":L"wb");free(wide);return result;
}
static int tb_cache_open(const char* path,int flags,...) {
    wchar_t* wide=tb_cache_wide(path);if(!wide)return -1;
    int result=_wopen(wide,flags|_O_BINARY,_S_IREAD|_S_IWRITE);free(wide);return result;
}
static int tb_cache_unlink(const char* path) {
    wchar_t* wide=tb_cache_wide(path);if(!wide)return -1;
    int result=_wunlink(wide);free(wide);return result;
}
static int tb_cache_rename(const char* from,const char* to) {
    wchar_t* a=tb_cache_wide(from),*b=tb_cache_wide(to);
    int ok=a&&b&&MoveFileExW(a,b,MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH);
    free(a);free(b);return ok?0:-1;
}
static int tb_cache_flock(int descriptor,int operation) {
    HANDLE handle=(HANDLE)_get_osfhandle(descriptor);OVERLAPPED position={0};
    if(operation==8)return UnlockFileEx(handle,0,1,0,&position)?0:-1;
    return LockFileEx(handle,LOCKFILE_EXCLUSIVE_LOCK,0,1,0,&position)?0:-1;
}
static int tb_cache_mkstemp(char* path) {
    size_t n=strlen(path);if(n<6)return -1;
    for(unsigned attempt=0;attempt<128;++attempt) {
        unsigned random=0;if(rand_s(&random))return -1;
        snprintf(path+n-6,7,"%06X",random&0xffffff);
        int fd=tb_cache_open(path,_O_RDWR|_O_CREAT|_O_EXCL);
        if(fd>=0)return fd;
    }
    return -1;
}
#define fopen tb_cache_fopen
#define open tb_cache_open
#define close _close
#define write _write
#define unlink tb_cache_unlink
#define remove tb_cache_unlink
#define rename tb_cache_rename
#define flock tb_cache_flock
#define mkstemp tb_cache_mkstemp
#define fsync _commit
#define O_CLOEXEC _O_NOINHERIT
#define LOCK_EX 2
#define LOCK_UN 8
