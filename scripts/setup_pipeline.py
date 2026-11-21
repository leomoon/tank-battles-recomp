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


def translation_signature(args, config, resolve, prepare, legacy=False):
    """Identify translations by content, not location, so moving the builder
    resources or the workspace keeps them. `legacy` reproduces the earlier
    absolute-path form so existing setups are not retranslated."""
    sdk = resolve(config["paths"]["sdk"])
    sources = sorted((sdk / "tools").rglob("*.py"))
    name = (lambda path: str(path)) if legacy else (lambda path: path.relative_to(sdk).as_posix())
    recipe = dict(elf=digest(Path(args.elf)), chunk_lines=config["recompiler"]["chunk_lines"],
                  prepare=inspect.getsource(prepare),
                  sources={name(path): digest(path) for path in sources})
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def packaged_inputs(args):
    """Digests of the selected runtime bundle and builder toolchain identities."""
    identities = {}
    for name, filename in (("runtime_bundle", "manifest.json"), ("toolchain", "toolchain.json")):
        root = getattr(args, "selected_" + name, None)
        if root:
            identities[name] = digest(Path(root) / filename)
    return identities


def configuration_signature(args, config, resolve, generated):
    root = Path(__file__).resolve().parents[1]
    sdk = resolve(config["paths"]["sdk"])
    ours = [path for path in (root / "cmake").rglob("*") if path.suffix == ".cmake" or path.name == "CMakeLists.txt"]
    paths = sorted(set(root.glob("*.cmake")) | set(root.glob("CMakeLists.txt")) | set(ours)
                   | set(sdk.rglob("*.cmake")) | set(sdk.rglob("CMakeLists.txt")))
    recipe = dict(sources={str(path): digest(path) for path in paths}, generated=generated,
                  elf=str(Path(args.elf)), elf_sha256=digest(Path(args.elf)), sdk=str(sdk), recompiled=str(resolve(config["paths"]["recompiled"])),
                  options={name: getattr(args, name, None) for name in ("build_type", "backend", "cmake", "ninja", "cmake_args", "runtime_bundle", "runtime_source", "toolchain")},
                  packaged=packaged_inputs(args),
                  environment={key: os.environ.get(key) for key in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "CMAKE_PREFIX_PATH", "CMAKE_TOOLCHAIN_FILE", "PKG_CONFIG_PATH")})
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def configuration_outputs(build_dir):
    """CMakeCache.txt plus the generator's own build file (Ninja or Visual Studio)."""
    build_dir = Path(build_dir)
    cache = build_dir / "CMakeCache.txt"
    generated = [build_dir / "build.ninja", *sorted(build_dir.glob("*.sln"))]
    generated = [path for path in generated if path.is_file()]
    if not cache.is_file() or not generated:
        return {}
    return {str(path): digest(path) for path in (cache, *generated)}


def player_executable(build_dir, args):
    build_dir = Path(build_dir)
    if os.name != "nt":
        return build_dir / "TankBattlesRecomp"
    return build_dir / ("Release/TankBattlesRecomp.exe" if getattr(args, "multi_config", False) else "TankBattlesRecomp.exe")


STAGE_NAMES = {"configure": "configure", "build": "compile"}


def run_pipeline(args, config, resolve, prepare, configure, build, notify=None):
    """A failed stage never receives a completion stamp. Retry the same command.

    `notify(stage, state)` receives translate/configure/compile with started,
    reused or done, for front ends that show stage progress."""
    notify = notify or (lambda stage, state: None)
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
    if (not args.restart and current and state.get("outputs") == current and state.get("signature")
            and state["signature"] != signature and state["signature"] == translation_signature(args, config, resolve, prepare, legacy=True)):
        state["signature"] = signature   # Same content; only the recorded form changed.
        atomic_json(marker, state)
    if not args.restart and current and state.get("signature") == signature and state.get("outputs") == current:
        print("[setup] prepare: verified translations reused.", flush=True)
        notify("translate", "reused")
    else:
        notify("translate", "started")
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
            notify("translate", "done")
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
            notify("configure", "reused")
            continue
        executable = player_executable(build_dir, args)
        if name == "build" and state.get("executable_sha256") and executable.is_file() and digest(executable) != state["executable_sha256"]:
            print("[setup] build: executable changed outside setup; requesting a fresh link.", flush=True)
            executable.unlink()
        print(f"[setup] {name}: checking incremental dependencies.", flush=True)
        notify(STAGE_NAMES[name], "started")
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
        notify(STAGE_NAMES[name], "done")


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
