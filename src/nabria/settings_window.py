"""The settings window: model, microphone, history.

An ordinary toplevel, not a layer surface -- this one is meant to behave like
any other window, so it takes the system decorations and the tiling rules that
come with them. It runs inside the daemon rather than as a second process
because everything it edits is live state the daemon already owns: a separate
process could write the config file but could not make the running daemon
re-read it, could not tear down the loaded whisper server, and could not know
what the recorder is doing.

The three panels are the three questions this tool could not answer when it
appeared to be dead: which model is loaded, which microphone is being heard,
and what was actually transcribed.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import audio, config, history, models

# Both lists come from the modules that own them rather than being restated
# here. They used to be restated, and both copies had gone stale: the model
# labels named only the two large variants, so anyone who took the setup
# wizard's own recommendation saw a raw "ggml-base.bin" in the picker, and the
# language list knew nothing about the dialect prompt -- so choosing Arabic in
# the wizard shipped the Levantine prompt and choosing it here did not.
LANGUAGES = [(code, preset["label"]) for code, preset in config.LANGUAGE_PRESETS.items()]

_BY_FILENAME = {model.filename: model for model in models.CATALOG.values()}


def _model_label(name: str) -> str:
    model = _BY_FILENAME.get(name)
    return f"{model.key} — {model.summary}" if model else name


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        application: Gtk.Application,
        settings: dict,
        on_change: Callable[[str, object], None],
    ):
        super().__init__(application=application, title="Dictation")
        self.settings = settings
        self.on_change = on_change
        self.set_default_size(560, 640)

        notebook = Gtk.Notebook()
        notebook.set_margin_top(12)
        notebook.set_margin_bottom(12)
        notebook.set_margin_start(12)
        notebook.set_margin_end(12)
        notebook.append_page(self._engine_page(), Gtk.Label(label="Engine"))
        notebook.append_page(self._microphone_page(), Gtk.Label(label="Microphone"))
        notebook.append_page(self._history_page(), Gtk.Label(label="History"))
        self.set_child(notebook)

    # -- engine ------------------------------------------------------------

    def _engine_page(self) -> Gtk.Widget:
        box = _page()

        models = config.models()
        self.model_paths = [str(path) for path in models]
        self.model_combo = Gtk.ComboBoxText()
        for path in models:
            self.model_combo.append_text(_model_label(path.name))
        current = str(self.settings.get("model", ""))
        if current in self.model_paths:
            self.model_combo.set_active(self.model_paths.index(current))
        self.model_combo.connect("changed", self._on_model_changed)
        box.append(_row("Model", self.model_combo))
        box.append(
            _hint(
                "Switching unloads the running server; the next dictation "
                "loads the new model while you speak."
            )
        )

        self.language_combo = Gtk.ComboBoxText()
        for _, label in LANGUAGES:
            self.language_combo.append_text(label)
        codes = [code for code, _ in LANGUAGES]
        current_language = str(self.settings.get("language", "ar"))
        self.language_combo.set_active(
            codes.index(current_language) if current_language in codes else 0
        )
        self.language_combo.connect("changed", self._on_language_changed)
        box.append(_row("Language", self.language_combo))

        vocabulary = Gtk.Entry()
        vocabulary.set_hexpand(True)
        vocabulary.set_text(str(self.settings.get("vocabulary", "")))
        # Committed when the edit is finished, not on "changed": that fires per
        # keystroke, so typing one term rewrote the config file and tore the
        # loaded model down once per character, and persisted the prompt in
        # half-typed states along the way.
        vocabulary.connect("activate", self._on_vocabulary_changed)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c, entry=vocabulary: self._on_vocabulary_changed(entry))
        vocabulary.add_controller(focus)
        box.append(_row("Vocabulary", vocabulary))
        box.append(
            _hint(
                "Fed to whisper as its initial prompt. Mixing Arabic and Latin "
                "here is what keeps spoken tech terms spelled rather than "
                "transliterated. Keep it short — a long prompt leaks into the "
                "transcript."
            )
        )
        return box

    def _on_model_changed(self, combo: Gtk.ComboBoxText) -> None:
        index = combo.get_active()
        if 0 <= index < len(self.model_paths):
            self.on_change("model", self.model_paths[index])

    def _on_language_changed(self, combo: Gtk.ComboBoxText) -> None:
        index = combo.get_active()
        if 0 <= index < len(LANGUAGES):
            self.on_change("language", LANGUAGES[index][0])

    def _on_vocabulary_changed(self, entry: Gtk.Entry) -> None:
        self.on_change("vocabulary", entry.get_text())

    # -- microphone --------------------------------------------------------

    def _microphone_page(self) -> Gtk.Widget:
        box = _page()

        self.source_combo = Gtk.ComboBoxText()
        self.source_ids: list[int] = []
        self._reload_sources()
        self.source_combo.connect("changed", self._on_source_changed)
        box.append(_row("Input", self.source_combo))

        self.level_label = Gtk.Label(xalign=0.0)
        self.level_label.set_wrap(True)
        self.level_label.set_text("Not measured yet.")
        box.append(self.level_label)

        button = Gtk.Button(label="Test microphone (4s)")
        button.set_halign(Gtk.Align.START)
        button.connect("clicked", self._on_test_clicked)
        box.append(button)
        keep = Gtk.CheckButton(label="Keep the recording of every dictation")
        keep.set_active(bool(self.settings.get("keep_audio")))
        keep.connect("toggled", self._on_keep_toggled)
        box.append(keep)
        box.append(
            _hint(
                "Kept takes appear with a play button in History, which is the "
                "only way to tell a misheard word from a badly spoken one. They "
                "are never deleted automatically — a day of dictation is a lot "
                "of audio."
            )
        )

        threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
        box.append(
            _hint(
                f"Speak while it records. Anything at or below {threshold:.0f} "
                "dBFS is discarded as silence rather than transcribed, so a "
                "reading below that while you are speaking means the level is "
                "too low — not that dictation is broken."
            )
        )
        return box

    def _reload_sources(self) -> None:
        self.source_combo.remove_all()
        self.source_ids = []
        try:
            found = audio.sources()
        except audio.AudioError:
            found = []
        active = 0
        for index, source in enumerate(found):
            suffix = " (muted)" if source["muted"] else ""
            self.source_combo.append_text(f"{source['name']}{suffix}")
            self.source_ids.append(source["id"])
            if source["default"]:
                active = index
        if self.source_ids:
            # set_active fires "changed", which would immediately re-assert the
            # current default as a user choice. Harmless, but it also logs, so
            # the handler is muted while the list is being populated.
            self._loading_sources = True
            self.source_combo.set_active(active)
            self._loading_sources = False

    def _on_source_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_loading_sources", False):
            return
        index = combo.get_active()
        if not (0 <= index < len(self.source_ids)):
            return
        node_id = self.source_ids[index]
        self.level_label.set_text("Switching input…")

        # Off the main thread: set_default shells out to wpctl with a five
        # second timeout, and a wedged PipeWire is exactly the fault this
        # window exists to diagnose. Blocking here would freeze the window and
        # the daemon's main loop -- the orb with it.
        def work() -> None:
            try:
                audio.set_default(node_id)
                text = "Input switched. Test it to see the level."
            except audio.AudioError as exc:
                text = f"Could not switch input: {exc}"
            GLib.idle_add(self._set_level_text, text)

        threading.Thread(target=work, daemon=True, name="nabria-set-source").start()

    def _set_level_text(self, text: str) -> bool:
        self.level_label.set_text(text)
        return GLib.SOURCE_REMOVE

    def _on_keep_toggled(self, button: Gtk.CheckButton) -> None:
        self.on_change("keep_audio", button.get_active())

    def _on_test_clicked(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        self.level_label.set_text("Recording…")

        def work() -> None:
            try:
                level = audio.measure(4.0)
                name = (audio.default_source() or {}).get("name", "the default input")
                text = f"{name}: {level:.1f} dBFS"
                threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
                text += (
                    "  — above the gate, speech will be transcribed."
                    if level > threshold
                    else f"  — at or below the {threshold:.0f} dBFS gate, "
                    "this would be discarded as silence."
                )
            except audio.AudioError as exc:
                text = f"Test failed: {exc}"
            GLib.idle_add(self._test_done, button, text)

        threading.Thread(target=work, daemon=True, name="nabria-mic-test").start()

    def _test_done(self, button: Gtk.Button, text: str) -> bool:
        self.level_label.set_text(text)
        button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    # -- history -----------------------------------------------------------

    def _history_page(self) -> Gtk.Widget:
        box = _page()
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        records = history.recent(200)
        if not records:
            listbox.append(Gtk.Label(label="No transcripts yet.", margin_top=12))
        for record in records:
            listbox.append(_history_row(record))

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(listbox)
        box.append(scroller)
        return box


def _history_row(record: dict) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row.set_margin_top(6)
    row.set_margin_bottom(6)
    row.set_margin_start(6)
    row.set_margin_end(6)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    meta = Gtk.Label(xalign=0.0)
    meta.set_markup(
        f"<small>{GLib.markup_escape_text(str(record.get('at', '')))}"
        f"  ·  {record.get('seconds', 0)}s</small>"
    )
    meta.add_css_class("dim-label")
    header.append(meta)

    # Only offered when the file is still there: audio is kept per-setting and
    # never cleaned up automatically, so old entries and deleted files are both
    # normal and neither should produce a button that does nothing.
    audio = str(record.get("audio", ""))
    if audio and Path(audio).exists():
        play = Gtk.Button(label="▶")
        play.add_css_class("flat")
        play.set_tooltip_text(audio)
        play.connect("clicked", lambda _b, path=audio: _play(path))
        header.append(play)
    row.append(header)

    # No explicit direction: Pango resolves it from the text itself, which is
    # what makes an Arabic transcript read right-to-left and a Latin one
    # left-to-right in the same list without either being forced.
    text = Gtk.Label(xalign=0.0, label=str(record.get("text", "")))
    text.set_wrap(True)
    text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    text.set_selectable(True)
    row.append(text)
    return row


def _play(path: str) -> None:
    """Play a kept take. Detached, so a slow player cannot stall the UI."""
    player = shutil.which("pw-play") or shutil.which("paplay")
    if not player:
        return
    with contextlib.suppress(OSError):
        subprocess.Popen(
            [player, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


# -- small builders --------------------------------------------------------


def _page() -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for setter in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
        getattr(box, setter)(14)
    return box


def _row(label: str, widget: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    caption = Gtk.Label(label=label, xalign=0.0)
    caption.set_size_request(90, -1)
    box.append(caption)
    widget.set_hexpand(True)
    box.append(widget)
    return box


def _hint(text: str) -> Gtk.Widget:
    label = Gtk.Label(label=text, xalign=0.0)
    label.set_wrap(True)
    label.add_css_class("dim-label")
    return label
