#pragma once
/* Loader and queue-lock adapters only. Preserve Vulkan submission semantics. */
#ifdef _WIN32
#include <windows.h>
typedef CRITICAL_SECTION pthread_mutex_t;
static int pthread_mutex_init(pthread_mutex_t* mutex,void* unused) {(void)unused;InitializeCriticalSection(mutex);return 0;}
static int pthread_mutex_lock(pthread_mutex_t* mutex) {EnterCriticalSection(mutex);return 0;}
static int pthread_mutex_unlock(pthread_mutex_t* mutex) {LeaveCriticalSection(mutex);return 0;}
static int pthread_mutex_destroy(pthread_mutex_t* mutex) {DeleteCriticalSection(mutex);return 0;}
#define RTLD_NOW 0
#define RTLD_LOCAL 0
static void* dlopen(const char* name,int flags) {(void)name;(void)flags;return (void*)LoadLibraryExW(L"vulkan-1.dll",NULL,LOAD_LIBRARY_SEARCH_SYSTEM32);}
static void* dlsym(void* module,const char* name) {return (void*)GetProcAddress((HMODULE)module,name);}
static int dlclose(void* module) {return FreeLibrary((HMODULE)module)?0:-1;}
static const char* dlerror(void) {return "Windows Vulkan loader unavailable; install the GPU driver";}
#else
#include <dlfcn.h>
#include <pthread.h>
#endif
