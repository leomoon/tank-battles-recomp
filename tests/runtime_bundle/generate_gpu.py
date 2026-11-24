"""Reuse the actual-backend Linux fixtures without changing their assertions."""
import ast
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
overlay=Path(sys.argv[2])
tree=ast.parse((root/'scripts/test_vulkan_draw_groups.py').read_text())
hooks=next(node.value for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str) and 'tb_render_clock(unsigned' in node.value)
hooks=hooks.replace('__thread const char* tb_wait_label;', '#ifdef _WIN32\n__declspec(thread) const char* tb_wait_label;\n#else\n__thread const char* tb_wait_label;\n#endif\n')
(out/'hooks.c').write_text(hooks)
for name in ('draw_groups','depth16','nv3089','render_scale'):
    includes = [overlay/'libs/video/rsx_vulkan_backend.c']
    if name == 'render_scale':
        includes.append(root/'sdk_overrides/render_scale_shader.h')
    includes.append(root/('tests/runtime_bundle/nv3089.inc' if name=='nv3089' else 'tests/vulkan_'+name+'.inc'))
    (out/(name+'.c')).write_text(
        '#include "host_services.h"\n#define setenv tb_host_env\n#ifdef _WIN32\n'
        '#define unsetenv(name) tb_host_env(name,"",1)\n#endif\n' +
        ''.join(f'#include "{path.as_posix()}"\n' for path in includes)
    )
