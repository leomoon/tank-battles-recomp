"""Tank Battles builder: pick your purchased files, press Build, then Play.

The window drives scripts/setup_engine.py (one JSON event per line) in a child
process, so it stays responsive and Cancel stops exactly the build's own
processes. Rebuilding into an existing player folder keeps its saves,
settings and online accounts.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from builder_session import FIELDS, ROOT, STAGE_SYMBOLS, STAGES, BuilderSession, browse_output, output_folder, play
from player_folder import default_output, launcher_name


def run_tk_window(session: BuilderSession, self_test: bool) -> int:
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
    saved = session.saved
    fields = {key: tk.StringVar(value=saved.get(key, "")) for key in ("pkg", "unlock", "elf", "keys", "rap", "workspace")}
    fields["output"] = tk.StringVar(value=saved.get("output") or str(default_output()))
    jobs = tk.StringVar(value=str(saved.get("jobs") or ""))
    advanced = tk.BooleanVar(value=bool(saved.get("keys") or saved.get("rap") or saved.get("workspace")))

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

    for key, label, patterns in FIELDS[:4]:
        picker(label, key, [(label, " ".join(patterns))], folder=not patterns)
    extra = ttk.Frame(frame)
    extra.columnconfigure(1, weight=1)
    toggle = ttk.Checkbutton(frame, text="Advanced", variable=advanced,
                             command=lambda: extra.grid() if advanced.get() else extra.grid_remove())
    toggle.grid(row=row, column=0, sticky="w", pady=(4, 0))
    row += 1
    extra.grid(row=row, column=0, columnspan=3, sticky="ew")
    row += 1
    for line, (key, label, patterns) in enumerate(FIELDS[4:]):
        picker(label, key, [(label, " ".join(patterns))], folder=not patterns, parent=extra, at=line)
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

    def values() -> dict:
        chosen = {key: variable.get().strip() for key, variable in fields.items()}
        chosen["jobs"] = int(jobs.get()) if jobs.get().strip().isdigit() and int(jobs.get()) > 0 else None
        return chosen

    def set_running(running: bool):
        for widget in pickers + [toggle]:
            widget.configure(state="disabled" if running else "normal")
        build_button.configure(state="disabled" if running else "normal")
        cancel_button.configure(state="normal" if running else "disabled")

    def refresh():
        model = session.model
        running = session.engine is not None
        message.configure(text=session.message, foreground={True: "dark green", False: "dark red", None: ""}[session.success])
        set_running(running)
        for name, label in STAGES:
            marks[name].configure(text=STAGE_SYMBOLS.get(model.stages[name], "  ") + label)
        if model.progress and model.progress[1]:
            bar.stop()
            bar.configure(mode="determinate", value=1000 * model.progress[0] / model.progress[1])
        elif running:
            if bar["mode"] != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start(15)
        else:
            bar.stop()
            bar.configure(mode="determinate", value=1000 if session.success else 0)
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.insert("end", "\n".join(model.lines[-400:]))
        log_box.see("end")
        log_box.configure(state="disabled")
        log_label.configure(text=f"Log: {model.log_path}" if model.log_path else "")
        if session.success:
            play_button.configure(state="normal")

    def poll():
        if session.poll():
            refresh()
        if session.engine:
            root.after(150, poll)

    def start_build():
        if session.start(values()):
            play_button.configure(state="disabled")
            root.after(150, poll)
        refresh()

    def cancel_build():
        session.cancel()
        refresh()

    def close():
        if session.engine:
            if not messagebox.askyesno("Tank Battles Builder", "A build is running. Cancel it and close?"):
                return
            session.cancel()
            session.engine.process.wait(timeout=60)
        root.destroy()

    build_button.configure(command=start_build)
    cancel_button.configure(command=cancel_build)
    play_button.configure(command=lambda: play(output_folder(values())))
    folder_button.configure(command=lambda: browse_output(values()))
    root.protocol("WM_DELETE_WINDOW", close)
    if (Path(fields["output"].get()) / launcher_name()).is_file():
        play_button.configure(state="normal")
    if self_test:
        root.after(500, root.destroy)
    root.mainloop()
    return 0


def run_window(self_test: bool = False) -> int:
    session = BuilderSession()
    if os.name == "nt" or sys.platform == "linux":
        from builder_gtk import run_gtk_window

        status = run_gtk_window(session, self_test)
        if status is not None:
            return status
    return run_tk_window(session, self_test)


def main() -> int:
    if "--self-test" in sys.argv:
        # Packaging check: resources verify and the window opens and closes.
        from install_layout import Layout
        Layout(ROOT, os.environ.get("TB_WORKSPACE") or Path.home() / ".cache/tank-battles-self-test").verify()
        return run_window(self_test=True)
    return run_window()


if __name__ == "__main__":
    sys.exit(main())
