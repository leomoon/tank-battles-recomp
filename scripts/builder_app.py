"""Tank Battles builder: pick your purchased files, press Build, then Play.

The window drives scripts/setup_engine.py (one JSON event per line) in a child
process, so it stays responsive and Cancel stops exactly the build's own
processes. Rebuilding into an existing player folder keeps its saves,
settings and online accounts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from player_folder import default_output, launcher_name  # noqa: E402
from setup_engine import STAGES  # noqa: E402

LOG_LINES = 4000


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "TankBattlesRecomp"
    else:
        config = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(config) if config and Path(config).is_absolute() else Path.home() / ".config") / "tank-battles-recomp"
    return base / "builder.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(values: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n")


def engine_command(values: dict) -> list[str]:
    """The setup engine invocation for the chosen inputs. Paths only; no secrets."""
    command = [sys.executable, "-E", "-s", str(ROOT / "scripts/setup_engine.py"), "--json", "--stdin-control"]
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


def run_window(self_test: bool = False) -> int:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Tank Battles Builder")
    # Many Linux sessions report 96 DPI whatever the screen; size text for the
    # actual height as well, so a 4K desktop does not get tiny type.
    dpi = root.winfo_fpixels("1i")
    factor = max(dpi / 96, root.winfo_screenheight() / 1080)
    root.tk.call("tk", "scaling", 96 * factor / 72)
    root.geometry(f"{int(760 * factor)}x{int(620 * factor)}")
    root.minsize(int(640 * factor), int(520 * factor))
    saved = load_settings()
    fields = {key: tk.StringVar(value=saved.get(key, "")) for key in ("pkg", "unlock", "elf", "keys", "rap", "workspace")}
    fields["output"] = tk.StringVar(value=saved.get("output") or str(default_output()))
    jobs = tk.StringVar(value=str(saved.get("jobs") or ""))
    advanced = tk.BooleanVar(value=bool(saved.get("keys") or saved.get("rap") or saved.get("workspace")))
    state = {"engine": None, "model": Model()}

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text="Build your own Tank Battles for this computer", font=("TkDefaultFont", 13, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(frame, wraplength=int(700 * factor), justify="left",
              text="Select the files from your purchase. The builder translates and compiles the game on this "
                   "computer; nothing is downloaded and nothing leaves it.").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

    row = 2
    pickers = []

    def picker(label, key, kinds, folder=False, parent=frame, at=None):
        nonlocal row
        line = row if at is None else at
        ttk.Label(parent, text=label).grid(row=line, column=0, sticky="w", padx=(0, 8), pady=2)
        entry = ttk.Entry(parent, textvariable=fields[key])
        entry.grid(row=line, column=1, sticky="ew", pady=2)

        def browse():
            chosen = (filedialog.askdirectory(initialdir=fields[key].get() or str(Path.home())) if folder else
                      filedialog.askopenfilename(filetypes=kinds + [("All files", "*")]))
            if chosen:
                fields[key].set(chosen)
        button = ttk.Button(parent, text="Browse...", command=browse)
        button.grid(row=line, column=2, padx=(6, 0), pady=2)
        pickers.extend([entry, button])
        if at is None:
            row += 1

    picker("Game package (.pkg)", "pkg", [("PlayStation packages", "*.pkg *.PKG")])
    picker("Unlock (.edat)", "unlock", [("Unlock files", "*.edat *.EDAT")])
    picker("Decrypted EBOOT.elf", "elf", [("Executables", "*.elf *.ELF")])
    picker("Install to", "output", [], folder=True)
    extra = ttk.Frame(frame)
    extra.columnconfigure(1, weight=1)
    toggle = ttk.Checkbutton(frame, text="Advanced", variable=advanced,
                             command=lambda: extra.grid() if advanced.get() else extra.grid_remove())
    toggle.grid(row=row, column=0, sticky="w", pady=(4, 0))
    row += 1
    extra.grid(row=row, column=0, columnspan=3, sticky="ew")
    row += 1
    picker("SELF keys (instead of an ELF)", "keys", [("Key files", "*")], parent=extra, at=0)
    picker("RAP (license-2 unlocks only)", "rap", [("RAP files", "*.rap *.RAP")], parent=extra, at=1)
    picker("Workspace (several GB)", "workspace", [], folder=True, parent=extra, at=2)
    ttk.Label(extra, text="Parallel jobs (empty: automatic)").grid(row=3, column=0, sticky="w", pady=2)
    ttk.Spinbox(extra, from_=1, to=64, textvariable=jobs, width=6).grid(row=3, column=1, sticky="w", pady=2)
    if not advanced.get():
        extra.grid_remove()

    stages_frame = ttk.Frame(frame)
    stages_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 4))
    row += 1
    marks = {}
    for index, (name, label) in enumerate(STAGES):
        marks[name] = ttk.Label(stages_frame, text="  " + label, width=34)
        marks[name].grid(row=index // 2, column=index % 2, sticky="w")
    bar = ttk.Progressbar(frame, mode="determinate", maximum=1000)
    bar.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
    row += 1
    message = ttk.Label(frame, wraplength=int(700 * factor), justify="left")
    message.grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1
    log_box = tk.Text(frame, height=10, wrap="none", state="disabled", font="TkFixedFont")
    log_box.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=4)
    frame.rowconfigure(row, weight=1)
    row += 1
    log_label = ttk.Label(frame, text="")
    log_label.grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1
    buttons = ttk.Frame(frame)
    buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(8, 0))
    build_button = ttk.Button(buttons, text="Build")
    cancel_button = ttk.Button(buttons, text="Cancel", state="disabled")
    play_button = ttk.Button(buttons, text="Play", state="disabled")
    folder_button = ttk.Button(buttons, text="Open folder")
    for button in (folder_button, play_button, cancel_button, build_button):
        button.pack(side="left", padx=4)

    symbols = {"waiting": "  ", "started": "> ", "reused": "= ", "done": "+ ", "failed": "x ", "cancelled": "- "}

    def values() -> dict:
        chosen = {key: variable.get().strip() for key, variable in fields.items()}
        chosen["jobs"] = int(jobs.get()) if jobs.get().strip().isdigit() and int(jobs.get()) > 0 else None
        return chosen

    def set_running(running: bool):
        for widget in pickers + [toggle]:
            widget.configure(state="disabled" if running else "normal")
        build_button.configure(state="disabled" if running else "normal")
        cancel_button.configure(state="normal" if running else "disabled")

    def refresh(model: Model):
        for name, label in STAGES:
            marks[name].configure(text=symbols.get(model.stages[name], "  ") + label)
        if model.progress and model.progress[1]:
            bar.configure(mode="determinate")
            bar.stop()
            bar["value"] = 1000 * model.progress[0] / model.progress[1]
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.insert("end", "\n".join(model.lines[-400:]))
        log_box.see("end")
        log_box.configure(state="disabled")
        if model.log_path:
            log_label.configure(text=f"Log: {model.log_path}")

    def poll():
        engine = state["engine"]
        if engine is None:
            return
        model = state["model"]
        finished = False
        try:
            while True:
                event = engine.events.get_nowait()
                if event["event"] == "exit":
                    finished = True
                else:
                    model.apply(event)
                    if event["event"] == "stage" and event["state"] == "started":
                        bar.configure(mode="indeterminate")
                        bar.start(15)
        except queue.Empty:
            pass
        refresh(model)
        if not finished:
            root.after(150, poll)
            return
        bar.stop()
        state["engine"] = None
        set_running(False)
        result = model.result or {"ok": False, "error": "The build stopped unexpectedly.", "action": "Press Build to retry."}
        if result.get("ok"):
            bar.configure(mode="determinate")
            bar["value"] = 1000
            output = Path(values()["output"] or default_output())
            message.configure(foreground="dark green",
                              text=f"Done. Your game is in {output}. Press Play, or start \"{launcher_name()}\" there. "
                                   f"Saves and settings live in {output / 'game'} and are kept when you rebuild.")
            play_button.configure(state="normal")
            current = values()
            current["previous_build"] = True
            save_settings(current)
        else:
            details = "\n".join(model.problems())
            message.configure(foreground="dark red",
                              text=f"{result.get('error', '')} {result.get('action', '')}" + (f"\n{details}" if details else ""))

    def start_build():
        chosen = values()
        chosen["previous_build"] = load_settings().get("previous_build")
        problems = check_values(chosen)
        if problems:
            message.configure(foreground="dark red", text="\n".join(problems))
            return
        save_settings({k: v for k, v in chosen.items()})
        state["model"] = Model()
        message.configure(foreground="", text="Building. This takes a while the first time; you can cancel and continue later.")
        play_button.configure(state="disabled")
        set_running(True)
        state["engine"] = Engine(engine_command(chosen))
        root.after(150, poll)

    def cancel_build():
        if state["engine"]:
            message.configure(text="Cancelling; finished stages are kept.")
            state["engine"].cancel()

    def close():
        if state["engine"]:
            if not messagebox.askyesno("Tank Battles Builder", "A build is running. Cancel it and close?"):
                return
            state["engine"].cancel()
            state["engine"].process.wait(timeout=60)
        root.destroy()

    def open_output():
        output = Path(values()["output"] or default_output())
        open_path(output if output.exists() else output.parent if output.parent.exists() else Path.home())

    build_button.configure(command=start_build)
    cancel_button.configure(command=cancel_build)
    play_button.configure(command=lambda: play(Path(values()["output"] or default_output())))
    folder_button.configure(command=open_output)
    root.protocol("WM_DELETE_WINDOW", close)
    if (Path(fields["output"].get()) / launcher_name()).is_file():
        play_button.configure(state="normal")
    if self_test:
        root.after(500, root.destroy)
    root.mainloop()
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        # Packaging check: resources verify and the window opens and closes.
        from install_layout import Layout
        Layout(ROOT, os.environ.get("TB_WORKSPACE") or Path.home() / ".cache/tank-battles-self-test").verify()
        return run_window(self_test=True)
    return run_window()


if __name__ == "__main__":
    sys.exit(main())
