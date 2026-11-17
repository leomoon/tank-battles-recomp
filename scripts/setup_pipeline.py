"""Resume translation safely; let CMake and Ninja track native build dependencies."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile

from setup_inputs import digest


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def outputs(directory):
    directory = Path(directory)
    paths = sorted(path for path in directory.glob("ppu_recomp*") if path.is_file())
    if not (directory / "ppu_recomp.h").is_file() or not any(path.suffix in (".c", ".cpp") for path in paths):
        return {}
    return {path.name: digest(path) for path in paths}


def translation_signature(args, config, resolve, prepare):
    sdk = resolve(config["paths"]["sdk"])
    sources = sorted((sdk / "tools").rglob("*.py"))
    recipe = dict(elf=digest(Path(args.elf)), chunk_lines=config["recompiler"]["chunk_lines"],
                  prepare=inspect.getsource(prepare),
                  sources={str(path): digest(path) for path in sources})
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def configuration_signature(args, config, resolve, generated):
    root = Path(__file__).resolve().parents[1]
    sdk = resolve(config["paths"]["sdk"])
    paths = sorted(set(root.glob("*.cmake")) | set(root.glob("CMakeLists.txt"))
                   | set(sdk.rglob("*.cmake")) | set(sdk.rglob("CMakeLists.txt")))
    recipe = dict(sources={str(path): digest(path) for path in paths}, generated=generated,
                  elf=str(Path(args.elf)), elf_sha256=digest(Path(args.elf)), sdk=str(sdk), recompiled=str(resolve(config["paths"]["recompiled"])),
                  options={name: getattr(args, name, None) for name in ("build_type", "backend", "cmake", "ninja", "cmake_args", "runtime_bundle", "runtime_source")},
                  environment={key: os.environ.get(key) for key in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "CMAKE_PREFIX_PATH", "CMAKE_TOOLCHAIN_FILE", "PKG_CONFIG_PATH")})
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def configuration_outputs(build_dir):
    paths = [build_dir / name for name in ("CMakeCache.txt", "build.ninja")]
    return {str(path): digest(path) for path in paths} if all(path.is_file() for path in paths) else {}


def run_pipeline(args, config, resolve, prepare, configure, build):
    """A failed stage never receives a completion stamp. Retry the same command."""
    destination = resolve(config["paths"]["recompiled"])
    marker = resolve(config["paths"]["gamedata_root"]) / "setup-state.json"
    try:
        state = json.loads(marker.read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    signature = translation_signature(args, config, resolve, prepare)
    current = outputs(destination)
    if not args.restart and current and state.get("signature") == signature and state.get("outputs") == current:
        print("[setup] prepare: verified translations reused.", flush=True)
    else:
        print("[setup] prepare: translating into staging; existing output stays intact on failure.", flush=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix=".tank-battles-translation-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "recompiled"
                staged_config = deepcopy(config)
                staged_config["paths"]["recompiled"] = str(staged)
                prepare(args, staged_config)
                generated = outputs(staged)
                if not generated:
                    raise ValueError("Translation produced no complete PPU output; retry setup after checking the prepare output.")
                if generated != current:
                    previous = Path(temporary) / "previous"
                    if destination.exists():
                        destination.rename(previous)
                    try:
                        staged.rename(destination)
                    except OSError:
                        if previous.exists():
                            previous.rename(destination)
                        raise
                else:
                    print("[setup] prepare: output is identical; retaining existing file timestamps.", flush=True)
            state.update(signature=signature, outputs=generated, completed="prepare")
            atomic_json(marker, state)
        except Exception as error:
            raise ValueError("Setup prepare failed. Correct the error and rerun the same setup command. " + str(error)) from error
    build_dir = resolve(getattr(args, "build_dir", None) or config["paths"]["build"])
    configure_signature = configuration_signature(args, config, resolve, state["outputs"])
    artifacts = configuration_outputs(build_dir)
    # Ninja always checks native AND CMake regeneration dependencies. Avoid an
    # unconditional manual configure, which re-emits the SDK's SPU sources.
    for name, action in (("configure", configure), ("build", build)):
        if name == "configure" and artifacts and state.get("configuration") == configure_signature and state.get("configuration_outputs") == artifacts:
            print("[setup] configure: verified configuration reused; Ninja will check regeneration dependencies.", flush=True)
            continue
        executable = build_dir / ("Release/TankBattlesRecomp.exe" if os.name == "nt" and getattr(args, "runtime_bundle", None)
                                  else "TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
        if name == "build" and state.get("executable_sha256") and executable.is_file() and digest(executable) != state["executable_sha256"]:
            print("[setup] build: executable changed outside setup; requesting a fresh link.", flush=True)
            executable.unlink()
        print(f"[setup] {name}: checking incremental dependencies.", flush=True)
        try:
            action(args, config)
        except Exception as error:
            raise ValueError(f"Setup {name} failed. Correct the error and rerun setup; completed work will be reused. {error}") from error
        state["completed"] = name
        if name == "build":
            if not executable.is_file():
                raise ValueError("Setup build finished without TankBattlesRecomp. Check the native build output and rerun setup.")
            state["executable_sha256"] = digest(executable)
        state["configuration"] = configure_signature
        state["configuration_outputs"] = configuration_outputs(build_dir)
        atomic_json(marker, state)


@contextmanager
def setup_lock(data):
    # Lock import as well as translation/build so two commands cannot replace
    # each other's installed title. Checks remain read-only and take no lock.
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    with (data / "setup-state.lock").open("a") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if lock.tell() == lock.seek(0, os.SEEK_END):
                lock.write("0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("Another setup is using this game. Wait for it to finish, then retry.") from None
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another setup is using this game. Wait for it to finish, then retry.") from None
            yield
