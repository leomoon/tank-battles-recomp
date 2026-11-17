"""Generate fixture-only ABI declarations and production source copies."""
import subprocess
import ast
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[2]
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
sdk=root/'external/ps3recomp'
sys.path.insert(0,str(sdk/'tools'))
from ppu_lifter import PPULifter
(out/'ppu_recomp.h').write_text(PPULifter().emit_header())
subprocess.run([sys.executable,str(root/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)

def literal(path, token):
    tree=ast.parse(path.read_text())
    return next(node.value for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str) and token in node.value)

# Reuse the existing semantic fixtures, adapting only their host allocation,
# sleep and test-thread scaffolding. Production source copies are unchanged.
def portable(source):
    source=source.replace('#include <sys/mman.h>','').replace('#include <unistd.h>','')
    source=source.replace('#include <pthread.h>','')
    source=source.replace('mmap(NULL,0x10000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)','calloc(1,0x10000)')
    source=source.replace('mmap(NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0)','calloc(1,0x10000)')
    source=source.replace('vm_base!=MAP_FAILED','vm_base!=NULL').replace('vm_base != MAP_FAILED','vm_base != NULL')
    source=source.replace('usleep(', 'tb_host_sleep_us(').replace('setenv(', 'tb_host_env(')
    source=source.replace('pthread_t guest;assert(!pthread_create(&guest,NULL,poll_guest,NULL));','SDL_Thread* guest=SDL_CreateThread(poll_guest,"fixture poll",NULL);assert(guest);')
    source=source.replace('static void* poll_guest(void* unused)','static int poll_guest(void* unused)').replace('    return NULL;','    return 0;').replace('pthread_join(guest,NULL)','SDL_WaitThread(guest,NULL)')
    return '#include "'+(root/'host_services.h').as_posix()+'"\n'+source

(out/'cellPad_under_test.c').write_text((out/'overlay/libs/input/cellPad.c').read_text())
for name in ('positions','hotplug'):
    (out/(name+'.c')).write_text(portable((root/('tests/input_'+('positions' if name=='positions' else 'hotplug_concurrent')+'.c')).read_text()))
keyboard=(sdk/'libs/input/tests/test_pad_keyboard.c').read_text().replace('#include "../cellPad.c"','#include "cellPad_under_test.c"')
keyboard+='\nvoid tb_input_sample(uint32_t k,uint32_t p,uint32_t b){(void)k;(void)p;(void)b;}\n'
keyboard=keyboard.replace('    /* Several keys at once. */','    keys(0,0,0);tb_keyboard_down(SDL_SCANCODE_Z);pad_poll_backend();tb_host_sleep_us(20000);pad_poll_backend();assert(tb_keyboard_peek()==CELL_PAD_CTRL_CROSS);assert(D2()==CROSS);assert(D2()==0);tb_keyboard_down(SDL_SCANCODE_DOWN);assert(D1()==DOWN);assert(D1()==0);\n    /* Several keys at once. */')
# Give fixture SDL hooks private names: static SDL's dynapi object exports
# all entry points together, so ordinary symbol interposition cannot work.
keyboard_hooks=('SDL_NumJoysticks','SDL_PeepEvents','SDL_WasInit','SDL_InitSubSystem',
                'SDL_GetError','SDL_GameControllerUpdate','SDL_GetKeyboardState','SDL_GetKeyboardFocus')
private_hooks=''.join('#define '+name+' tb_fixture_'+name+'\n' for name in keyboard_hooks)
(out/'keyboard.c').write_text(private_hooks+portable(keyboard))

audio=(out/'overlay/libs/audio/cellAudio.c').read_text()
audio='#include <stdatomic.h>\nstatic atomic_uint observed_blocks;\n'+audio
audio=audio.replace('static void audio_notify_event_queues(void)\n{','static void audio_notify_event_queues(void)\n{\natomic_fetch_add(&observed_blocks,1);')
extra=literal(root/'scripts/test_audio_clock.py','max_queued_frames=')
extra=extra.replace(' struct timespec begin,end;clock_gettime(CLOCK_MONOTONIC,&begin);',' uint64_t begin=tb_host_now();')
extra=extra.replace(' clock_gettime(CLOCK_MONOTONIC,&end);\n double seconds=(end.tv_sec-begin.tv_sec)+(end.tv_nsec-begin.tv_nsec)/1e9;',' double seconds=(tb_host_now()-begin)/1e9;').replace('usleep(1000)','tb_host_sleep_us(1000)')
extra='#ifdef _WIN32\n#include <timeapi.h>\nstatic void fixture_end_timer(void){timeEndPeriod(1);}\n#endif\n'+extra
extra=extra.replace(' assert(cellAudioInit()==0);','\n#ifdef _WIN32\n assert(timeBeginPeriod(1)==TIMERR_NOERROR);atexit(fixture_end_timer);\n#endif\n assert(cellAudioInit()==0);')
(out/'audio.c').write_text(audio+extra+'\nuint32_t sys_event_queue_create_direct(uint64_t key,int32_t size){(void)key;(void)size;return 0;}\n')

cache=literal(root/'scripts/test_edat_cache.py','original_edat_resolve')
helper=(sdk/'libs/filesystem/tests/test_edat_guest_klicensee.c').read_text().split('\nint main(')[0]
helper=helper.replace('#include "../edat.c"','#include "'+(sdk/'libs/filesystem/edat.c').as_posix()+'"')
cache=cache.replace('#include "external/ps3recomp/libs/filesystem/tests/test_edat_guest_klicensee.c"',helper)
cache=cache.replace('        raise(SIGSTOP);','        for(;;)tb_host_sleep_us(100000);')
cache='#include "'+(root/'host_services.h').as_posix()+'"\n#ifdef _WIN32\n#include "'+(root/'sdk_overrides/cache_windows.inc').as_posix()+'"\n#endif\n'+cache
cache=cache.replace('int main(int argc,char** argv)', '#ifdef _WIN32\nstatic int cache_fixture_main(int argc,char** argv)\n#else\nint main(int argc,char** argv)\n#endif\n')
cache+='\n#ifdef _WIN32\n#include "'+(root/'host_utf8_args.h').as_posix()+'"\nTB_UTF8_ENTRY(cache_fixture_main)\n#endif\n'
(out/'cache.c').write_text(cache)
