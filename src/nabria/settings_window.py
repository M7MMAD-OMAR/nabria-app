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

from . import audio, config, history, i18n, models

# Both lists come from the modules that own them rather than being restated
# here. They used to be restated, and both copies had gone stale: the model
# labels named only the two large variants, so anyone who took the setup
# wizard's own recommendation saw a raw "ggml-base.bin" in the picker, and the
# language list knew nothing about the dialect prompt -- so choosing Arabic in
# the wizard shipped the Levantine prompt and choosing it here did not.
LANGUAGES = [(code, preset["label"]) for code, preset in config.LANGUAGE_PRESETS.items()]

# What the app itself is written in, which is not what you dictate in. `auto`
# first, because following the desktop is the right answer for most people and
# the only one that stays right when they change their desktop's language.
# Derived, not restated -- same rule as LANGUAGES above, and for the same
# reason: a third language added to i18n and forgotten here would render
# everywhere and be impossible to choose.
UI_LANGUAGES = [("auto", "settings.ui_language.auto")] + [
    (code, f"language.{code}.label") for code in i18n.LANGUAGES
]

_BY_FILENAME = {model.filename: model for model in models.CATALOG.values()}


def _model_label(name: str) -> str:
    """`base — Fast on any machine.`, or the bare filename for a stranger.

    Both halves are isolated: the model name is Latin, and so is a filename
    dropped into the model directory by hand, which is the case this falls
    back to.
    """
    model = _BY_FILENAME.get(name)
    if not model:
        return i18n.ltr(name)
    return f"{i18n.ltr(model.key)} — {i18n.t(model.summary)}"


class _Confirm(Gtk.Button):
    """A button that asks before it does it, without a dialog.

    Press once and it becomes the question; press again and it happens;
    press anything else, or leave, and it goes back to being a button. That is
    the whole mechanism, and it is here rather than `Gtk.AlertDialog` because
    that arrived in GTK 4.10 and Debian stable ships 4.8 -- so a dialog means
    two code paths for one question, on a control whose entire job is to be
    unambiguous.

    It also reads better for what these actually are. Deleting a 1.6 GB model
    or every transcript you have ever taken deserves a second press; it does
    not deserve a modal window over the top of the thing you were looking at.
    """

    def __init__(self, label: str, question: str, on_confirm):
        super().__init__(label=label)
        self.quiet = label
        self.question = question
        self.on_confirm = on_confirm
        self.armed = False
        self.connect("clicked", self._pressed)

    def _pressed(self, _button) -> None:
        if not self.armed:
            self.armed = True
            self.set_label(self.question)
            self.add_css_class("destructive-action")
            return
        self.disarm()
        self.on_confirm()

    def disarm(self) -> None:
        self.armed = False
        self.set_label(self.quiet)
        self.remove_css_class("destructive-action")


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        application: Gtk.Application,
        settings: dict,
        on_change: Callable[[str, object], None],
        on_toggle: Callable[[], None] | None = None,
        state: Callable[[], str] | None = None,
    ):
        super().__init__(application=application, title=i18n.t("settings.title"))
        self.settings = settings
        self.on_change = on_change
        self.on_toggle = on_toggle
        self.read_state = state
        self.record_source = 0
        self.set_default_size(560, 640)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self._record_row())
        self.set_child(column)

        # Kept on the window, not just held in this scope. The screenshot
        # script has to select a tab and ask which one is showing, and reaching
        # in through `get_child()` to find it broke silently the moment this
        # window grew a row above the notebook -- the picture that came out was
        # of the tabs with the new row cropped away, and the traceback was
        # printed where nothing was reading it.
        self.notebook = notebook = Gtk.Notebook()
        notebook.set_margin_top(12)
        notebook.set_margin_bottom(12)
        notebook.set_margin_start(12)
        notebook.set_margin_end(12)
        notebook.append_page(self._engine_page(), Gtk.Label(label=i18n.t("settings.tab.engine")))
        notebook.append_page(self._microphone_page(),
                             Gtk.Label(label=i18n.t("settings.tab.microphone")))
        notebook.append_page(self._history_page(),
                             Gtk.Label(label=i18n.t("settings.tab.history")))
        notebook.set_vexpand(True)
        column.append(notebook)

    # -- dictating from the window ------------------------------------------

    def _record_row(self) -> Gtk.Widget:
        """A button that takes a dictation, for when no key can be bound.

        The hotkey is the product and this is not a second way of doing the
        same thing for the sake of it: on GNOME and KDE the shortcut is a
        settings dialog somebody has to find, on a locked-down desktop there
        may be no way to bind a key at all, and until that is done the
        application is installed and unusable. The launcher entry already opens
        this window for exactly that reason -- it just had nothing here to
        press.

        Only when the daemon handed us a way to ask. Constructed without one --
        which is how the screenshots and the tests build it -- there is no
        button, rather than a button that does nothing.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if self.on_toggle is None:
            return box
        box.set_margin_top(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.record = Gtk.Button(label=i18n.t("settings.record.start"))
        self.record.add_css_class("suggested-action")
        self.record.connect("clicked", lambda _button: self.on_toggle())
        box.append(self.record)
        box.append(_hint(i18n.t("settings.record.hint")))

        # Polled rather than pushed. The daemon's state changes on its own --
        # a take finishes transcribing with nobody touching this window -- and
        # a poll that lives and dies with the window cannot leave a callback
        # pointing at a destroyed one, which a subscription would have to be
        # careful about on every path out of here.
        # The source is created before the first refresh, not after it: the
        # refresh checks the source to know whether it is still wanted, so
        # doing it the other way round made the very first one a no-op and the
        # button opened saying "Start speaking" over a take already recording.
        self.record_source = GLib.timeout_add(200, self._refresh_record)
        self._refresh_record()
        self.connect("close-request", self._stop_polling)
        return box

    def _refresh_record(self) -> bool:
        """Say what pressing it will do now, not what it did when it was built."""
        # Two ways this window can go, and only one of them is a signal.
        # `close-request` is emitted when the *user* closes it; the screenshot
        # script and the daemon both end it with `destroy()`, which emits
        # nothing this can hang a handler on. Measured: after `destroy()` the
        # source stayed live and went on setting a label on a button inside a
        # destroyed window, holding the whole widget tree alive -- one leak per
        # picture taken. A destroyed window has been dropped from its
        # application -- measured, `get_application()` returns None afterwards
        # and never does before -- so asking that is exact, and true whichever
        # way the window went. `get_root()` is not: it still answers with the
        # window itself after it has been destroyed.
        if self.record_source == 0 or self.get_application() is None:
            self.record_source = 0
            return GLib.SOURCE_REMOVE
        state = self.read_state() if self.read_state else "idle"
        self.record.set_label(i18n.t({
            "recording": "settings.record.stop",
            "working": "settings.record.working",
        }.get(state, "settings.record.start")))
        # Working is not a state with an action in it: the take is already
        # recorded and is being typed, and there is nothing to start or stop.
        self.record.set_sensitive(state != "working")
        return GLib.SOURCE_CONTINUE

    def _stop_polling(self, *_args) -> bool:
        if self.record_source:
            GLib.source_remove(self.record_source)
            self.record_source = 0
        return False

    def _retitle_delete(self) -> None:
        """Name the model in the question, and say when it is the last one.

        "Delete?" over a combo box is a question about whichever entry happens
        to be selected, which is exactly the ambiguity a confirmation exists to
        remove.
        """
        index = self.model_combo.get_active()
        if not 0 <= index < len(self.model_paths):
            self.delete_model.set_sensitive(False)
            return
        self.delete_model.set_sensitive(True)
        name = Path(self.model_paths[index]).stem.removeprefix("ggml-")
        self.delete_model.question = i18n.t(
            "settings.model.remove.confirm", model=i18n.ltr(name)
        )
        self.delete_model.disarm()

    def _remove_model(self) -> None:
        index = self.model_combo.get_active()
        if not 0 <= index < len(self.model_paths):
            return
        path = Path(self.model_paths[index])
        freed = 0
        with contextlib.suppress(OSError):
            freed = path.stat().st_size
        if not models.remove(path):
            return

        self.model_paths.pop(index)
        self.model_combo.remove(index)
        message = i18n.t("settings.model.removed", megabytes=round(freed / 1_000_000))
        if not self.model_paths:
            # Not an error, and not a state to leave silently: `needs_setup`
            # opens the wizard when there is no model, so say that is what will
            # happen rather than letting a window appear out of nowhere.
            message += " " + i18n.t("settings.model.remove.last")
        else:
            self.model_combo.set_active(0)
            self.on_change("model", self.model_paths[0])
        self.model_note.set_text(message)
        self.model_note.set_visible(True)
        self._retitle_delete()

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

        # A model is the largest thing this program puts on anyone's disk --
        # 1.6 GB at the top of the catalogue -- and until now the only way to
        # get that back was to know where the directory was. Something that can
        # spend a gigabyte and a half without asking should be able to give it
        # back without being read about.
        picker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.model_combo.set_hexpand(True)
        picker.append(self.model_combo)
        self.delete_model = _Confirm(
            i18n.t("settings.model.remove"), i18n.t("settings.model.remove.confirm",
                                                    model=""), self._remove_model,
        )
        picker.append(self.delete_model)
        box.append(_row(i18n.t("settings.model"), picker))

        self.model_note = _hint("")
        self.model_note.set_visible(False)
        box.append(self.model_note)
        box.append(_hint(i18n.t("settings.model.hint")))
        self._retitle_delete()

        box.append(_row(i18n.t("settings.language"),
                        self._picker("language", LANGUAGES)))

        # The interface's own language, next to the dictation language on
        # purpose -- the two sit next to each other in the config file and in
        # everyone's head, and the only way to show they are different settings
        # is to show them together and label them apart.
        box.append(_row(i18n.t("settings.ui_language"),
                        self._picker("ui_language", UI_LANGUAGES)))
        box.append(_hint(i18n.t("settings.ui_language.hint")))

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
        box.append(_row(i18n.t("settings.vocabulary"), vocabulary))
        box.append(_hint(i18n.t("settings.vocabulary.hint")))
        return box

    def _picker(self, setting: str, choices: list[tuple[str, str]]) -> Gtk.Widget:
        """A combo box over `[(code, label_key)]` that writes `setting`.

        Both language pickers were the same fourteen lines with two names
        changed, down to the `index(current) if current in codes else 0`. The
        next setting shaped like this would have been a third copy.
        """
        combo = Gtk.ComboBoxText()
        codes = [code for code, _ in choices]
        for _, label in choices:
            combo.append_text(i18n.t(label))
        current = str(self.settings.get(setting, ""))
        combo.set_active(codes.index(current) if current in codes else 0)

        def changed(widget: Gtk.ComboBoxText) -> None:
            index = widget.get_active()
            if 0 <= index < len(codes):
                self.on_change(setting, codes[index])

        combo.connect("changed", changed)
        return combo

    def _on_model_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._retitle_delete()
        index = combo.get_active()
        if 0 <= index < len(self.model_paths):
            self.on_change("model", self.model_paths[index])

    def _on_vocabulary_changed(self, entry: Gtk.Entry) -> None:
        self.on_change("vocabulary", entry.get_text())

    # -- microphone --------------------------------------------------------

    def _microphone_page(self) -> Gtk.Widget:
        box = _page()

        self.source_combo = Gtk.ComboBoxText()
        self.source_ids: list[int] = []
        self._reload_sources()
        self.source_combo.connect("changed", self._on_source_changed)
        box.append(_row(i18n.t("settings.input"), self.source_combo))

        self.level_label = i18n.label()
        self.level_label.set_wrap(True)
        self.level_label.set_text(i18n.t("settings.not_measured"))
        box.append(self.level_label)

        button = Gtk.Button(label=i18n.t("settings.test"))
        button.set_halign(Gtk.Align.START)
        button.connect("clicked", self._on_test_clicked)
        box.append(button)
        keep = Gtk.CheckButton(label=i18n.t("settings.keep_audio"))
        keep.set_active(bool(self.settings.get("keep_audio")))
        keep.connect("toggled", self._on_keep_toggled)
        box.append(keep)
        box.append(_hint(i18n.t("settings.keep_audio.hint")))

        threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
        box.append(_hint(i18n.t(
            "settings.gate.hint", threshold=i18n.ltr(f"{threshold:.0f}")
        )))
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
            # The device name comes from PipeWire and is nearly always Latin,
            # so it is isolated rather than left to be reordered by an Arabic
            # window around it.
            name = i18n.ltr(source["name"])
            self.source_combo.append_text(
                i18n.t("settings.input.muted", name=name) if source["muted"] else name
            )
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
        self.level_label.set_text(i18n.t("settings.switching"))

        # Off the main thread: set_default shells out to wpctl with a five
        # second timeout, and a wedged PipeWire is exactly the fault this
        # window exists to diagnose. Blocking here would freeze the window and
        # the daemon's main loop -- the orb with it.
        def work() -> None:
            try:
                audio.set_default(node_id)
                text = i18n.t("settings.switched")
            except audio.AudioError as exc:
                text = i18n.t("settings.switch_failed", error=i18n.ltr(exc))
            GLib.idle_add(self._set_level_text, text)

        threading.Thread(target=work, daemon=True, name="nabria-set-source").start()

    def _set_level_text(self, text: str) -> bool:
        self.level_label.set_text(text)
        return GLib.SOURCE_REMOVE

    def _on_keep_toggled(self, button: Gtk.CheckButton) -> None:
        self.on_change("keep_audio", button.get_active())

    def _on_test_clicked(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        self.level_label.set_text(i18n.t("settings.recording"))

        def work() -> None:
            try:
                level = audio.measure(4.0)
                source = audio.default_source() or {}
                name = source.get("name") or i18n.t("settings.input")
                text = i18n.t("settings.level", name=i18n.ltr(name),
                              level=i18n.ltr(f"{level:.1f}"))
                threshold = float(self.settings.get("silence_threshold_dbfs", -42.0))
                text += (
                    i18n.t("settings.above_gate") if level > threshold
                    else i18n.t("settings.below_gate",
                                threshold=i18n.ltr(f"{threshold:.0f}"))
                )
            except audio.AudioError as exc:
                text = i18n.t("settings.test_failed", error=i18n.ltr(exc))
            GLib.idle_add(self._test_done, button, text)

        threading.Thread(target=work, daemon=True, name="nabria-mic-test").start()

    def _test_done(self, button: Gtk.Button, text: str) -> bool:
        self.level_label.set_text(text)
        button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    # -- history -----------------------------------------------------------

    def _history_page(self) -> Gtk.Widget:
        box = _page()
        self.history_list = Gtk.ListBox()
        self.history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._fill_history()

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.history_list)
        box.append(scroller)

        # Every word ever dictated on this machine is on this tab, and the
        # audio for any of it that was kept is beside it. A program that keeps
        # that has to be able to let go of it, in the window where it is being
        # looked at -- not by explaining which directory to empty.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        self.clear_history = _Confirm(
            i18n.t("settings.history.clear"),
            i18n.t("settings.history.clear.confirm", count=0),
            self._clear_history,
        )
        row.append(self.clear_history)
        box.append(row)

        self.history_note = _hint("")
        self.history_note.set_visible(False)
        box.append(self.history_note)
        self._retitle_clear()
        return box

    def _fill_history(self) -> None:
        while (child := self.history_list.get_first_child()) is not None:
            self.history_list.remove(child)
        records = history.recent(200)
        if not records:
            self.history_list.append(
                Gtk.Label(label=i18n.t("settings.no_transcripts"), margin_top=12)
            )
        for record in records:
            self.history_list.append(_history_row(record))

    def _retitle_clear(self) -> None:
        """Say how many, because "delete all" is a different question at 3
        transcripts than at two thousand."""
        count = len(history.recent(history.KEEP_LINES))
        self.clear_history.set_sensitive(count > 0)
        self.clear_history.question = i18n.t(
            "settings.history.clear.confirm", count=count
        )
        self.clear_history.disarm()

    def _clear_history(self) -> None:
        history.clear()
        self._fill_history()
        self.history_note.set_text(i18n.t("settings.history.cleared"))
        self.history_note.set_visible(True)
        self._retitle_clear()


def _history_row(record: dict) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row.set_margin_top(6)
    row.set_margin_bottom(6)
    row.set_margin_start(6)
    row.set_margin_end(6)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    meta = i18n.label()
    # The timestamp is isolated: it is a run of digits and punctuation, which
    # the bidirectional algorithm is free to rearrange against Arabic around
    # it, and a date that reads back wrong is worse than no date.
    stamp = GLib.markup_escape_text(i18n.ltr(record.get("at", "")))
    seconds = i18n.t("settings.seconds", seconds=record.get("seconds", 0))
    meta.set_markup(f"<small>{stamp}  ·  {seconds}</small>")
    meta.add_css_class("dim-label")
    header.append(meta)

    # Only offered when the file is still there: audio is kept per-setting and
    # never cleaned up automatically, so old entries and deleted files are both
    # normal and neither should produce a button that does nothing.
    audio = str(record.get("audio", ""))
    if audio and Path(audio).exists():
        play = Gtk.Button(label="▶")
        play.add_css_class("flat")
        play.set_tooltip_text(i18n.ltr(audio))
        play.connect("clicked", lambda _b, path=audio: _play(path))
        header.append(play)
    row.append(header)

    # No explicit direction: Pango resolves it from the text itself, which is
    # what makes an Arabic transcript read right-to-left and a Latin one
    # left-to-right in the same list without either being forced.
    text = i18n.label(str(record.get("text", "")))
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
    caption = i18n.label(label)
    caption.set_size_request(90, -1)
    box.append(caption)
    widget.set_hexpand(True)
    box.append(widget)
    return box


def _hint(text: str) -> Gtk.Widget:
    label = i18n.label(text)
    label.set_wrap(True)
    label.add_css_class("dim-label")
    return label
