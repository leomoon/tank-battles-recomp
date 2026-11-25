"""Check production dispatch and exact strict-stop behavior without a game."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner', required=True)
    args = parser.parse_args()
    runner = str(Path(args.runner).resolve())
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('TB_', 'PS3_', 'PPU_', 'BCTRL_', 'FLOW_', 'SPURS_'))}
    cases = [(None, 'strict', 0), ('--toc', 'strict', 0), ('--invalid', 'strict', 86),
             ('--null', 'strict', 86), ('--depth', 'strict', 86),
             ('--recovery', 'strict', 86),
             ('--invalid', 'diagnostic', 0), ('--depth', 'diagnostic', 0)]
    for argument, mode, expected in cases:
        case_env = dict(env, TB_EXECUTION_MODE=mode)
        if argument == '--toc':
            case_env['PPU_TOCFIX'] = '1'
        result = subprocess.run([runner] + ([argument] if argument else []),
                                env=case_env,
                                capture_output=True, text=True, timeout=15)
        log = result.stdout + result.stderr
        if result.returncode != expected:
            raise SystemExit(f'{argument} {mode}: expected {expected}, got {result.returncode}\n{log}')
        failure = 'ppu-recovery' if argument == '--recovery' else 'ppu-miss'
        if expected == 86 and 'STRICT STOP: ' + failure not in log:
            raise SystemExit('Strict stop lost its PPU failure report:\n' + log)
        if argument == '--invalid' and mode == 'diagnostic' and 'garbage vcall' not in log:
            raise SystemExit('Dispatch miss did not retain its diagnostic:\n' + log)
        print(f'PASS: {argument or "resolved dispatch"} {mode}')


if __name__ == '__main__':
    main()
