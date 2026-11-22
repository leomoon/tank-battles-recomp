"""Builder inputs, setup process, progress, and preferences shared by the desktop windows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from player_folder import default_output, launcher_name
from setup_engine import STAGES

ROOT = Path(__file__).resolve().parents[1]
LOG_LINES = 4000
FIELDS = (
    ("pkg", "Game package (.pkg)", ("*.pkg", "*.PKG")),
    ("unlock", "Unlock (.edat)", ("*.edat", "*.EDAT")),
    ("elf", "Decrypted EBOOT.elf", ("*.elf", "*.ELF")),
    ("output", "Install to", ()),
    ("keys", "SELF keys (instead of an ELF)", ("*",)),
    ("rap", "RAP (license-2 unlocks only)", ("*.rap", "*.RAP")),
    ("workspace", "Workspace (several GB)", ()),
)
STAGE_SYMBOLS = {"waiting": "  ", "started": "> ", "reused": "= ", "done": "+ ", "failed": "x ", "cancelled": "- "}


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "TankBattlesRecomp"
    else:
        config = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(config) if config and Path(config).is_absolute() else Path.home() / ".config") / "tank-battles-recomp"
    return base / "builder.json"


def load_settings() -> dict:
    try:
        values = json.loads(settings_path().read_text())
        return values if isinstance(values, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(values: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n")


def engine_command(values: dict) -> list[str]:
    """The setup engine invocation for the chosen inputs. Paths only; no secrets."""
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    command = [str(python), "-E", "-s", str(ROOT / "scripts/setup_engine.py"), "--json", "--stdin-control"]
    if values.get("workspace"):
        command += ["--workspace", values["workspace"]]
    for option, key in (("--pkg", "pkg"), ("--unlock", "unlock"), ("--elf", "elf"), ("--keys", "keys"), ("--rap", "rap")):
        if values.get(key):
            command += [option, values[key]]
    command += ["--output", values.get("output") or str(default_output())]
    if values.get("jobs"):
        command += ["--jobs", str(values["jobs"])]
    return command


def check_values(values: dict) -> list[str]:
    """Problems the window can name before starting; the engine checks the rest."""
    problems = []
    for key, label in (("pkg", "Game package"), ("unlock", "Unlock file"), ("elf", "Decrypted EBOOT.elf"),
                       ("keys", "SELF keys"), ("rap", "RAP file")):
        if values.get(key) and not Path(values[key]).is_file():
            problems.append(f"{label} not found: {values[key]}")
    if not values.get("pkg") and not values.get("previous_build"):
        problems.append("Choose your purchased Tank Battles package (.pkg).")
    return problems


class Model:
    """Build state from engine events, independent of the window toolkit."""

    def __init__(self):
        self.stages = {name: "waiting" for name, _ in STAGES}
        self.progress = None          # (done, total) for the running stage
        self.lines: list[str] = []
        self.result: dict | None = None
        self.log_path = None

    def apply(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "stage":
            self.stages[event["stage"]] = event["state"]
            if event["state"] in ("started", "done", "reused"):
                self.progress = None
        elif kind == "progress":
            self.progress = (event["done"], event["total"])
        elif kind == "log":
            self.lines.append(event["line"])
            del self.lines[:-LOG_LINES]
        elif kind == "result":
            self.result = event
            self.log_path = event.get("log")

    def problems(self) -> list[str]:
        """The failed input checks, as the engine printed them."""
        found, lines = [], self.lines
        for index, line in enumerate(lines):
            if line.startswith("[error] "):
                action = lines[index + 1].strip() if index + 1 < len(lines) and lines[index + 1].startswith("  ") else ""
                found.append(line[len("[error] "):] + (f": {action}" if action else ""))
        return found


class Engine:
    def __init__(self, command: list[str]):
        options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(command, **options)
        self.events: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.events.put(json.loads(line))
            except ValueError:
                self.events.put({"event": "log", "line": line})
        self.process.wait()
        self.events.put({"event": "exit", "status": self.process.returncode})

    def cancel(self):
        try:
            self.process.stdin.write("cancel\n")
            self.process.stdin.flush()
        except OSError:
            pass


class BuilderSession:
    """Shared build lifecycle for both desktop front ends."""

    def __init__(self):
        self.saved = load_settings()
        self.engine: Engine | None = None
        self.model = Model()
        self.message = ""
        self.success: bool | None = None
        self.output: Path | None = None

    def start(self, chosen: dict) -> bool:
        chosen["previous_build"] = self.saved.get("previous_build")
        problems = check_values(chosen)
        if problems:
            self.message, self.success = "\n".join(problems), False
            return False
        try:
            save_settings(chosen)
            self.engine = Engine(engine_command(chosen))
        except OSError as error:
            self.message, self.success = f"Could not start the build. {error}", False
            return False
        self.saved = chosen
        self.model = Model()
        self.output = Path(chosen["output"] or default_output())
        self.message, self.success = "Building. You can cancel and continue later.", None
        return True

    def poll(self) -> bool:
        if self.engine is None:
            return False
        changed = False
        while True:
            try:
                event = self.engine.events.get_nowait()
            except queue.Empty:
                return changed
            changed = True
            if event["event"] == "exit":
                break
            self.model.apply(event)
        self.engine = None
        result = self.model.result or {
            "ok": False, "error": "The build stopped unexpectedly.", "action": "Press Build to retry.",
        }
        self.success = bool(result.get("ok"))
        if self.success:
            self.message = f"Done. Your game is in {self.output}. Saves and settings are kept when you rebuild."
            self.saved["previous_build"] = True
            try:
                save_settings(self.saved)
            except OSError as error:
                self.message += f" Could not save the builder settings. {error}"
        else:
            details = "\n".join(self.model.problems())
            self.message = f"{result.get('error', '')} {result.get('action', '')}".strip()
            if details:
                self.message += f"\n{details}"
        return changed

    def cancel(self) -> None:
        if self.engine:
            self.message = "Cancelling. Finished stages are kept."
            self.engine.cancel()


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - opens the user's own folder
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play(output: Path) -> None:
    launcher = output / launcher_name()
    options = {"cwd": str(output)}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    subprocess.Popen([str(launcher)], **options)


def output_folder(values: dict) -> Path:
    return Path(values.get("output") or default_output())


def browse_output(values: dict) -> None:
    output = output_folder(values)
    open_path(output if output.exists() else output.parent if output.parent.exists() else Path.home())
