"""Read-only, best-effort host inventory for local performance evidence."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def command(args, timeout=40):
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=timeout)
        return dict(command=list(map(str, args)), returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(command=list(map(str, args)), unavailable=str(error))


def inventory():
    result = dict(system=platform.system(), release=platform.release(), version=platform.version(),
                  architecture=platform.machine(), logical_cpus=os.cpu_count())
    if os.name == 'nt':
        # Explicit fields omit serial numbers, accounts, network configuration and product keys.
        script = r'''
$ErrorActionPreference='Stop'
$queries=@{
 system=@('Win32_ComputerSystem',@('Manufacturer','Model','TotalPhysicalMemory'))
 cpu=@('Win32_Processor',@('Name','NumberOfCores','NumberOfLogicalProcessors','MaxClockSpeed'))
 gpu=@('Win32_VideoController',@('Name','PNPDeviceID','DriverVersion','DriverDate','CurrentHorizontalResolution','CurrentVerticalResolution','CurrentRefreshRate'))
 os=@('Win32_OperatingSystem',@('Caption','Version','BuildNumber'))
 memory=@('Win32_PhysicalMemory',@('Manufacturer','PartNumber','Capacity','Speed','ConfiguredClockSpeed','DeviceLocator'))
 storage=@('Win32_DiskDrive',@('Model','Size','InterfaceType'))
 audio=@('Win32_SoundDevice',@('Name','Status','Manufacturer'))
}
$result=@{}
foreach($key in $queries.Keys) {
 try { $result[$key]=@(Get-CimInstance -ClassName $queries[$key][0] | Select-Object -Property $queries[$key][1]) }
 catch { $result[$key]=@{unavailable=$_.Exception.Message} }
}
try { $result['power_scheme']=(& powercfg /getactivescheme | Out-String).Trim() }
catch { $result['power_scheme']=@{unavailable=$_.Exception.Message} }
$result | ConvertTo-Json -Depth 6 -Compress
'''
        probe = command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script])
        try:
            result['hardware'] = json.loads(probe['stdout'])
        except (KeyError, ValueError):
            result['hardware'] = probe
    else:
        result['cpu'] = command(['lscpu', '--json'])
        result['graphics'] = command(['lspci', '-nnk'])
        result['memory'] = command(['free', '-b'])
        result['os'] = Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else None
    result['thermal_conditions'] = dict(status='unavailable', reason='No validated temperature/power sampler; record external conditions per run.')
    return result


def capture(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = inventory()
    executable = shutil.which('vulkaninfo')
    if executable:
        # Old Intel SDK vulkaninfo lacks --summary; full text also retains formats and WSI.
        vulkan = command([executable, '--show-formats'], timeout=50)
        (directory/'vulkaninfo.txt').write_text(vulkan.pop('stdout', ''), encoding='utf-8')
        result['vulkan'] = dict(**vulkan, output='vulkaninfo.txt')
    else:
        result['vulkan'] = dict(unavailable='vulkaninfo not found; selected device can still be recorded from runtime logs')
    (directory/'host.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    capture(args.output)
    print('Host inventory written to', args.output)
