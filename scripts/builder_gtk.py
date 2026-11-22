"""Builder window using GTK4 through its C ABI.

The Windows builder brings its own GTK runtime. Linux uses the desktop library
when available, with the Tk window as an X11 fallback.
"""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
from types import SimpleNamespace

from builder_session import FIELDS, ROOT, STAGES, STAGE_SYMBOLS, BuilderSession, browse_output, output_folder, play
from player_folder import default_output, launcher_name

P, S, I, U, D = C.c_void_p, C.c_char_p, C.c_int, C.c_uint, C.c_double
ACCEPT, YES = -3, -8


def _bind_gtk(library: C.CDLL, glib: C.CDLL, gobject: C.CDLL, gio: C.CDLL) -> SimpleNamespace:
    signatures = {
        "g_set_prgname": (None, S),
        "gdk_set_allowed_backends": (None, S),
        "gdk_display_get_default": (P,),
        "g_type_name_from_instance": (S, P),
        "gtk_init_check": (I,),
        "g_main_loop_new": (P, P, I),
        "g_main_loop_run": (None, P),
        "g_main_loop_quit": (None, P),
        "g_main_loop_unref": (None, P),
        "g_timeout_add": (U, U, P, P),
        "g_source_remove": (I, U),
        "g_signal_connect_data": (C.c_ulong, P, S, P, P, P, I),
        "g_object_unref": (None, P),
        "g_free": (None, P),
        "g_file_new_for_path": (P, S),
        "g_file_get_path": (P, P),
        "gtk_window_new": (P,),
        "gtk_window_set_title": (None, P, S),
        "gtk_window_set_default_size": (None, P, I, I),
        "gtk_window_set_child": (None, P, P),
        "gtk_window_present": (None, P),
        "gtk_window_destroy": (None, P),
        "gtk_native_get_surface": (P, P),
        "gdk_surface_get_scale_factor": (I, P),
        "gtk_box_new": (P, I, I),
        "gtk_box_append": (None, P, P),
        "gtk_grid_new": (P,),
        "gtk_grid_attach": (None, P, P, I, I, I, I),
        "gtk_grid_set_row_spacing": (None, P, U),
        "gtk_grid_set_column_spacing": (None, P, U),
        "gtk_label_new": (P, S),
        "gtk_label_set_text": (None, P, S),
        "gtk_label_set_xalign": (None, P, C.c_float),
        "gtk_label_set_wrap": (None, P, I),
        "gtk_entry_new": (P,),
        "gtk_entry_set_placeholder_text": (None, P, S),
        "gtk_editable_get_text": (S, P),
        "gtk_editable_set_text": (None, P, S),
        "gtk_editable_set_width_chars": (None, P, I),
        "gtk_button_new_with_label": (P, S),
        "gtk_expander_new": (P, S),
        "gtk_expander_set_child": (None, P, P),
        "gtk_expander_set_expanded": (None, P, I),
        "gtk_progress_bar_new": (P,),
        "gtk_progress_bar_pulse": (None, P),
        "gtk_progress_bar_set_fraction": (None, P, D),
        "gtk_scrolled_window_new": (P,),
        "gtk_scrolled_window_set_child": (None, P, P),
        "gtk_scrolled_window_set_min_content_height": (None, P, I),
        "gtk_scrolled_window_set_policy": (None, P, I, I),
        "gtk_text_view_new": (P,),
        "gtk_text_view_set_editable": (None, P, I),
        "gtk_text_view_set_monospace": (None, P, I),
        "gtk_text_view_get_buffer": (P, P),
        "gtk_text_view_scroll_mark_onscreen": (None, P, P),
        "gtk_text_buffer_get_insert": (P, P),
        "gtk_text_buffer_set_text": (None, P, S, I),
        "gtk_widget_set_hexpand": (None, P, I),
        "gtk_widget_set_vexpand": (None, P, I),
        "gtk_widget_set_halign": (None, P, I),
        "gtk_widget_set_sensitive": (None, P, I),
        "gtk_widget_add_css_class": (None, P, S),
        "gtk_widget_remove_css_class": (None, P, S),
        "gtk_file_chooser_native_new": (P, S, P, I, S, S),
        "gtk_file_filter_new": (P,),
        "gtk_file_filter_set_name": (None, P, S),
        "gtk_file_filter_add_pattern": (None, P, S),
        "gtk_file_chooser_add_filter": (None, P, P),
        "gtk_file_chooser_set_current_folder": (I, P, P, P),
        "gtk_file_chooser_get_file": (P, P),
        "gtk_native_dialog_set_modal": (None, P, I),
        "gtk_native_dialog_show": (None, P),
        "gtk_native_dialog_destroy": (None, P),
        "gtk_message_dialog_new": (P, P, I, I, I, S),
    }
    for side in ("start", "end", "top", "bottom"):
        signatures[f"gtk_widget_set_margin_{side}"] = (None, P, I)
    # Older GTK4 releases report an integer surface scale.
    if hasattr(library, "gdk_surface_get_scale"):
        signatures["gdk_surface_get_scale"] = (D, P)
    gtk = SimpleNamespace()
    for name, (result, *arguments) in signatures.items():
        owner = library
        if name.startswith(("g_object_", "g_signal_", "g_type_")):
            owner = gobject
        elif name.startswith("g_file_"):
            owner = gio
        elif name.startswith("g_"):
            owner = glib
        function = getattr(owner, name)
        function.restype = result
        function.argtypes = arguments
        setattr(gtk, name, function)
    return gtk


def utf8(text: str) -> bytes:
    return text.encode("utf-8", errors="replace")


class Window:
    def __init__(self, gtk: SimpleNamespace, session: BuilderSession):
        self.gtk = gtk
        self.session = session
        self.loop = gtk.g_main_loop_new(None, False)
        self.window = gtk.gtk_window_new()
        self.callbacks = []
        self.fields = {}
        self.pickers = []
        self.choices = {}
        self.cancel_prompt = None
        self.closing = False
        self.timer = None
        self.last_log_text = ""

        gtk.gtk_window_set_title(self.window, b"Tank Battles Builder")
        gtk.gtk_window_set_default_size(self.window, 780, 640)
        outer = gtk.gtk_scrolled_window_new()
        gtk.gtk_scrolled_window_set_policy(outer, 2, 1)  # Never horizontal, automatic vertical.
        gtk.gtk_window_set_child(self.window, outer)
        form = gtk.gtk_box_new(1, 9)
        for side in ("start", "end", "top", "bottom"):
            getattr(gtk, f"gtk_widget_set_margin_{side}")(form, 20)
        gtk.gtk_scrolled_window_set_child(outer, form)
        heading = self.label("Build Tank Battles")
        gtk.gtk_widget_add_css_class(heading, b"title-1")
        gtk.gtk_box_append(form, heading)
        gtk.gtk_box_append(form, self.label("Choose your purchased game files and where to install the native player.", wrap=True))

        primary, advanced = self.grid(), self.grid()
        gtk.gtk_box_append(form, primary)
        saved = session.saved
        for index, (key, label, patterns) in enumerate(FIELDS):
            value = saved.get(key) or (str(default_output()) if key == "output" else "")
            self.add_field(primary if index < 4 else advanced, index % 4, key, label, patterns, value)
        self.add_field(advanced, 3, "jobs", "Parallel jobs", None, str(saved.get("jobs") or ""))
        gtk.gtk_entry_set_placeholder_text(self.fields["jobs"], b"Automatic")
        expander = gtk.gtk_expander_new(b"Advanced")
        gtk.gtk_expander_set_child(expander, advanced)
        gtk.gtk_expander_set_expanded(expander, any(saved.get(key) for key in ("keys", "rap", "workspace", "jobs")))
        gtk.gtk_box_append(form, expander)

        stages = self.grid()
        gtk.gtk_box_append(form, stages)
        self.marks = {}
        for index, (name, label) in enumerate(STAGES):
            mark = self.label(label)
            gtk.gtk_widget_set_hexpand(mark, True)
            self.marks[name] = mark
            gtk.gtk_grid_attach(stages, mark, index % 2, index // 2, 1, 1)
        self.bar = gtk.gtk_progress_bar_new()
        gtk.gtk_box_append(form, self.bar)
        self.message = self.label("", wrap=True)
        gtk.gtk_box_append(form, self.message)

        log_scroll = gtk.gtk_scrolled_window_new()
        gtk.gtk_scrolled_window_set_min_content_height(log_scroll, 110)
        gtk.gtk_widget_set_vexpand(log_scroll, True)
        self.log = gtk.gtk_text_view_new()
        gtk.gtk_text_view_set_editable(self.log, False)
        gtk.gtk_text_view_set_monospace(self.log, True)
        gtk.gtk_scrolled_window_set_child(log_scroll, self.log)
        gtk.gtk_box_append(form, log_scroll)
        self.log_label = self.label("", wrap=True)
        gtk.gtk_box_append(form, self.log_label)

        actions = gtk.gtk_box_new(0, 8)
        gtk.gtk_widget_set_halign(actions, 2)  # End.
        gtk.gtk_box_append(form, actions)
        for label, handler in (("Open folder", self.open_output), ("Play", self.play_output),
                               ("Cancel", self.cancel), ("Build", self.start)):
            button = self.button(label, handler)
            gtk.gtk_box_append(actions, button)
            if label == "Play":
                self.play_button = button
            elif label == "Cancel":
                self.cancel_button = button
            elif label == "Build":
                self.build_button = button
        gtk.gtk_widget_add_css_class(self.build_button, b"suggested-action")
        self.connect(self.window, "close-request", self.close, I)
        self.connect(self.fields["output"], "changed", lambda *_: self.update_play())
        self.choice_response = self.callback(self.chosen_file, None, P, I, P)
        self.cancel_response = self.callback(self.close_response, None, P, I, P)
        self.tick = self.callback(self.poll, I, P)
        self.refresh()

    def callback(self, handler, result, *arguments):
        callback = C.CFUNCTYPE(result, *arguments)(handler)
        self.callbacks.append(callback)
        return callback

    def connect(self, widget, signal, handler, result=None, *extra):
        callback = self.callback(handler, result, P, *extra, P)
        self.gtk.g_signal_connect_data(widget, signal.encode(), callback, None, None, 0)

    def label(self, text, wrap=False):
        label = self.gtk.gtk_label_new(utf8(text))
        self.gtk.gtk_label_set_xalign(label, 0)
        self.gtk.gtk_label_set_wrap(label, wrap)
        return label

    def grid(self):
        grid = self.gtk.gtk_grid_new()
        self.gtk.gtk_grid_set_row_spacing(grid, 6)
        self.gtk.gtk_grid_set_column_spacing(grid, 9)
        return grid

    def button(self, label, handler):
        button = self.gtk.gtk_button_new_with_label(utf8(label))
        self.connect(button, "clicked", lambda *_: handler())
        return button

    def add_field(self, grid, row, key, label, patterns, value):
        gtk = self.gtk
        entry = gtk.gtk_entry_new()
        gtk.gtk_editable_set_text(entry, utf8(value))
        gtk.gtk_editable_set_width_chars(entry, 10)
        gtk.gtk_widget_set_hexpand(entry, True)
        gtk.gtk_grid_attach(grid, self.label(label), 0, row, 1, 1)
        gtk.gtk_grid_attach(grid, entry, 1, row, 1, 1)
        self.fields[key] = entry
        self.pickers.append(entry)
        if patterns is not None:
            button = self.button("Browse…", lambda: self.browse(entry, label, patterns))
            gtk.gtk_grid_attach(grid, button, 2, row, 1, 1)
            self.pickers.append(button)
    def values(self):
        chosen = {key: self.gtk.gtk_editable_get_text(entry).decode("utf-8").strip()
                  for key, entry in self.fields.items()}
        jobs = chosen.pop("jobs")
        chosen["jobs"] = int(jobs) if jobs.isascii() and jobs.isdecimal() and len(jobs) <= 3 and int(jobs) else None
        return chosen

    def browse(self, entry, title, patterns):
        gtk = self.gtk
        folder = not patterns
        dialog = gtk.gtk_file_chooser_native_new(utf8(title), self.window, 2 if folder else 0, b"Select", b"Cancel")
        self.choices[dialog] = entry
        gtk.g_signal_connect_data(dialog, b"response", self.choice_response, None, None, 0)
        gtk.gtk_native_dialog_set_modal(dialog, True)
        for name, items in ((title, patterns), ("All files", ("*",))) if not folder else ():
            file_filter = gtk.gtk_file_filter_new()
            gtk.gtk_file_filter_set_name(file_filter, utf8(name))
            for pattern in items:
                gtk.gtk_file_filter_add_pattern(file_filter, utf8(pattern))
            gtk.gtk_file_chooser_add_filter(dialog, file_filter)
            gtk.g_object_unref(file_filter)
        value = gtk.gtk_editable_get_text(entry).decode("utf-8")
        start = Path(value).expanduser() if value else Path.home()
        if not start.is_dir():
            start = start.parent
        if start.is_dir():
            location = gtk.g_file_new_for_path(os.fsencode(start))
            gtk.gtk_file_chooser_set_current_folder(dialog, location, None)
            gtk.g_object_unref(location)
        gtk.gtk_native_dialog_show(dialog)

    def chosen_file(self, dialog, response, _data):
        gtk = self.gtk
        entry = self.choices.pop(dialog)
        if response == ACCEPT:
            file = gtk.gtk_file_chooser_get_file(dialog)
            if file:
                path = gtk.g_file_get_path(file)
                if path:
                    gtk.gtk_editable_set_text(entry, utf8(os.fsdecode(C.string_at(path))))
                    gtk.g_free(path)
                gtk.g_object_unref(file)
        gtk.gtk_native_dialog_destroy(dialog)
        gtk.g_object_unref(dialog)

    def update_play(self):
        output = output_folder(self.values())
        self.gtk.gtk_widget_set_sensitive(self.play_button, not self.session.engine and (output / launcher_name()).is_file())

    def open_output(self):
        self.run_action(browse_output)

    def play_output(self):
        self.run_action(lambda values: play(output_folder(values)))

    def run_action(self, action):
        try:
            action(self.values())
        except OSError as error:
            self.session.message, self.session.success = str(error), False
            self.refresh()

    def start(self):
        if self.session.start(self.values()):
            self.timer = self.gtk.g_timeout_add(150, self.tick, None)
        self.refresh()

    def cancel(self):
        self.session.cancel()
        self.refresh()

    def refresh(self):
        gtk, session = self.gtk, self.session
        running = session.engine is not None
        for widget in [*self.pickers, self.build_button]:
            gtk.gtk_widget_set_sensitive(widget, not running)
        gtk.gtk_widget_set_sensitive(self.cancel_button, running)
        self.update_play()
        gtk.gtk_label_set_text(self.message, utf8(session.message))
        for css, outcome in ((b"success", True), (b"error", False)):
            change = gtk.gtk_widget_add_css_class if session.success is outcome else gtk.gtk_widget_remove_css_class
            change(self.message, css)
        model = session.model
        for name, label in STAGES:
            gtk.gtk_label_set_text(self.marks[name], utf8(STAGE_SYMBOLS[model.stages[name]] + label))
        if model.progress and model.progress[1] and running:
            done, total = model.progress
            gtk.gtk_progress_bar_set_fraction(self.bar, max(0, min(1, done / total)))
        elif not running:
            gtk.gtk_progress_bar_set_fraction(self.bar, 1 if session.success else 0)
        text = "\n".join(model.lines[-400:])
        if text != self.last_log_text:
            buffer = gtk.gtk_text_view_get_buffer(self.log)
            gtk.gtk_text_buffer_set_text(buffer, utf8(text), -1)
            gtk.gtk_text_view_scroll_mark_onscreen(self.log, gtk.gtk_text_buffer_get_insert(buffer))
            self.last_log_text = text
        gtk.gtk_label_set_text(self.log_label, utf8(f"Log: {model.log_path}" if model.log_path else ""))

    def poll(self, _data):
        if self.session.poll():
            self.refresh()
        if self.session.engine:
            if not self.session.model.progress:
                self.gtk.gtk_progress_bar_pulse(self.bar)
            return True
        self.timer = None
        if self.closing:
            self.gtk.g_main_loop_quit(self.loop)
        return False

    def close(self, _window, _data):
        if not self.session.engine:
            self.gtk.g_main_loop_quit(self.loop)
        elif not self.closing and not self.cancel_prompt:
            gtk = self.gtk
            # Modal, question, yes/no. Use a fixed format with an explicit UTF-8 argument.
            self.cancel_prompt = gtk.gtk_message_dialog_new(self.window, 1, 2, 4, b"%s",
                                                            S(b"A build is running. Cancel it and close?"))
            gtk.g_signal_connect_data(self.cancel_prompt, b"response", self.cancel_response, None, None, 0)
            gtk.gtk_window_present(self.cancel_prompt)
        return True

    def close_response(self, dialog, response, _data):
        self.gtk.gtk_window_destroy(dialog)
        self.cancel_prompt = None
        if response == YES:
            self.closing = True
            self.cancel()

    def run(self, self_test):
        gtk = self.gtk
        gtk.gtk_window_present(self.window)
        if self_test:
            def finish(_data):
                surface = gtk.gtk_native_get_surface(self.window)
                get_scale = getattr(gtk, "gdk_surface_get_scale", gtk.gdk_surface_get_scale_factor)
                display = gtk.gdk_display_get_default()
                backend = gtk.g_type_name_from_instance(display).decode().removeprefix("Gdk").removesuffix("Display")
                print(f"Builder UI: {backend} (scale {get_scale(surface):g})", flush=True)
                gtk.g_main_loop_quit(self.loop)
                return False

            gtk.g_timeout_add(500, self.callback(finish, I, P), None)
        gtk.g_main_loop_run(self.loop)
        if self.timer:
            gtk.g_source_remove(self.timer)
        for dialog in self.choices:
            gtk.gtk_native_dialog_destroy(dialog)
            gtk.g_object_unref(dialog)
        gtk.gtk_window_destroy(self.window)
        gtk.g_main_loop_unref(self.loop)
        return 0


def _run(gtk: SimpleNamespace, backends: bytes, session: BuilderSession, self_test: bool) -> int | None:
    gtk.g_set_prgname(b"tank-battles-builder")
    gtk.gdk_set_allowed_backends(backends)
    if not gtk.gtk_init_check():
        return None
    return Window(gtk, session).run(self_test)


def run_gtk_window(session: BuilderSession, self_test: bool) -> int | None:
    if os.name == "nt":
        prefix = ROOT / "gtk"
        binaries = prefix / "bin"
        library = binaries / "libgtk-4-1.dll"
        # A developer running directly from the source tree can use the Tk UI.
        if not library.is_file() and not (ROOT / "package.json").is_file():
            return None
        os.environ.setdefault("GSK_RENDERER", "cairo")
        os.environ["XDG_DATA_DIRS"] = str(prefix / "share")
        os.environ["GSETTINGS_SCHEMA_DIR"] = str(prefix / "share/glib-2.0/schemas")
        with os.add_dll_directory(str(binaries)):
            gtk = _bind_gtk(C.CDLL(str(library)), C.CDLL(str(binaries / "libglib-2.0-0.dll")),
                            C.CDLL(str(binaries / "libgobject-2.0-0.dll")), C.CDLL(str(binaries / "libgio-2.0-0.dll")))
            status = _run(gtk, b"win32", session, self_test)
        if status is None:
            raise RuntimeError("Could not start the bundled GTK4 Windows window.")
        return status
    try:
        library = C.CDLL("libgtk-4.so.1")
    except OSError:
        return None
    gtk = _bind_gtk(library, library, library, library)
    backends = b"wayland,x11" if os.environ.get("WAYLAND_DISPLAY") else b"x11"
    return _run(gtk, backends, session, self_test)
