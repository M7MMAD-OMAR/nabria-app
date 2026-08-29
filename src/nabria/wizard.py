"""First run: get from "nothing installed" to "it typed my words".

Opened when `config.needs_setup` says so: on a first run, and again whenever
there is no model on disk. The second condition is why it cannot get stuck
marked done while the app is unusable -- it doubles as the repair path when
someone deletes a model.

Five steps and no more, because the whole promise is that there is nothing to
configure: say what you speak, pick a model, fetch it, check the microphone is
heard, learn the shortcut. Everything else has a sensible default and lives in
the settings window for the few who want it.

The styling comes from the shipped palette rather than the desktop theme, for
the same reason the indicator's does: this has to look considered on a bare
Sway session, not only on a fully themed desktop.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import audio, config, gpu, i18n, models, shortcut, theme  # noqa: E402

# Below this the microphone is under the silence gate and every take would be
# thrown away, so the test has to fail rather than politely report a number.
GOOD_ENOUGH_DBFS = -42.0

STYLE_TEMPLATE = """
window.nabria-setup, .nabria-setup {{ background-color: {surface}; color: {on_surface}; }}
.nabria-title {{ font-size: 20pt; font-weight: 700; }}
.nabria-lede {{ opacity: 0.72; }}
.nabria-card {{
  background-color: {card};
  border: 1px solid {outline};
  border-radius: 14px;
  padding: 14px;
}}
.nabria-card.selected {{ border-color: {primary}; }}
.nabria-choice-name {{ font-weight: 700; }}
.nabria-hint {{ opacity: 0.6; font-size: 10pt; }}
.nabria-good {{ color: {primary}; font-weight: 700; }}
.nabria-bad {{ color: {error}; font-weight: 700; }}
.nabria-key {{
  font-family: monospace;
  background-color: {card};
  border: 1px solid {outline};
  border-radius: 8px;
  padding: 8px 10px;
}}
/* The primary button carries the app's accent rather than the desktop's.
   GTK's stock suggested-action is the system accent -- blue on a default
   install -- which next to a coral indicator reads as two applications. */
.nabria-setup button.suggested-action {{
  background-image: none;
  background-color: {primary};
  color: {surface};
  font-weight: 700;
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
}}
.nabria-setup button.suggested-action:hover {{ background-color: {primary_hover}; }}
.nabria-setup button {{
  background-image: none;
  background-color: {card};
  color: {on_surface};
  border: 1px solid {outline};
  border-radius: 10px;
  padding: 8px 18px;
}}
.nabria-setup progressbar progress {{ background-color: {primary}; }}
.nabria-setup checkbutton radio:checked {{
  background-color: {primary};
  background-image: none;
  border-color: {primary};
  color: {surface};
}}
"""


def install_style(settings: dict) -> None:
    palette = theme.load(config.CONFIG_DIR, bool(settings.get("follow_desktop_palette")))
    theme.add_css(STYLE_TEMPLATE.format(
        surface=theme.to_hex(palette["surface_container"]),
        card=theme.to_hex(palette["card"]),
        outline=theme.to_hex(palette["outline_variant"]),
        primary=theme.to_hex(palette["primary"]),
        primary_hover=theme.to_hex(palette["primary"], lighten=0.12),
        error=theme.to_hex(palette["error"]),
        on_surface=theme.to_hex(palette["on_surface"]),
    ))


class Choice(Gtk.Box):
    """One selectable option, as a card rather than a combo box entry.

    A dropdown would hide exactly the information these choices turn on -- how
    big a download is, whether this machine can run it faster than speech.
    Both choice pages use this: the language page used to re-type the whole
    thing inline, so the two consecutive screens could drift apart.
    """

    def __init__(self, name: str, summary: str = "", *, note: str = "",
                 badge: str = "", trailing: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("nabria-card")

        self.radio = Gtk.CheckButton()
        self.radio.set_valign(Gtk.Align.CENTER)
        # The card carries the selection, not just the radio: at this size a
        # 16px dot is easy to miss, and the choice is the whole page.
        self.radio.connect("toggled", self._on_toggled)
        self.append(self.radio)

        # And the whole card is the target, not the dot. The card is already
        # what shows the selection and already carries everything the choice
        # turns on -- the size of the download, whether this machine can run
        # it -- so making the 16px circle the only thing that answers is a
        # smaller target than the thing being asked about.
        #
        # Selecting an already-selected radio in a group is a no-op, so a
        # click that lands on the radio itself is not counted twice.
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.radio.set_active(True))
        self.add_controller(click)
        # An invisible hit target is barely better than a small one.
        self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        column.set_hexpand(True)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = i18n.label(name)
        title.add_css_class("nabria-choice-name")
        heading.append(title)
        for text, style in ((trailing, "nabria-hint"), (badge, "nabria-good")):
            if not text:
                continue
            label = i18n.label(text)
            label.add_css_class(style)
            label.add_css_class("nabria-hint")
            heading.append(label)
        column.append(heading)

        if summary:
            body = i18n.label(summary, wrap=True)
            body.add_css_class("nabria-lede")
            column.append(body)

        # Reserved for the thing that turns a preference into a mistake.
        if note:
            warning = i18n.label(note, wrap=True)
            warning.add_css_class("nabria-bad")
            warning.add_css_class("nabria-hint")
            column.append(warning)

        self.append(column)

    def _on_toggled(self, button: Gtk.CheckButton) -> None:
        if button.get_active():
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


def _key_line(text: str, *, pasteable: bool = False) -> Gtk.Widget:
    """One boxed line on the shortcut page.

    `pasteable` marks the configuration to be copied, and carries the two
    things that make copying work:

    The line is laid out left-to-right at the *widget*, not by wrapping the
    text in isolate characters. Both fix the ordering an Arabic page would
    otherwise impose on `bind = CTRL ALT, Q`; only this one keeps U+2068 out of
    the clipboard, where it is invisible in the editor and fatal to the parser
    reading the file it lands in.

    And nothing here is focusable. A selectable label joins the focus chain, so
    the page opened with whichever line came first already highlighted, as
    though it had been chosen. Out of the chain, drag-to-select still works and
    the highlight is gone.
    """
    label = i18n.label(text, wrap=True, selectable=pasteable)
    label.set_can_focus(False)
    if pasteable:
        label.set_direction(Gtk.TextDirection.LTR)
        label.set_xalign(0.0)
    label.add_css_class("nabria-key")
    return label


def group(cards: list[Choice]) -> None:
    """Make a list of cards one radio group."""
    for card in cards[1:]:
        card.radio.set_group(cards[0].radio)


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, settings: dict, on_finished):
        super().__init__(application=application, title="Nabria")
        self.settings = settings
        self.on_finished = on_finished
        self.set_default_size(560, 560)
        # Five fixed steps with nothing to resize and nothing to keep open, so
        # it asks to be a fixed panel rather than a window. Whether it gets
        # one is the compositor's decision; measured, Hyprland honours the
        # hint and floats it at exactly this size. Where a compositor does not,
        # the README gives the window rule -- keyed on the title, which is the
        # product name and is never translated.
        #
        # The settings window deliberately does *not* do this: it has a
        # scrollable transcript list, which is the one place resizing earns
        # its keep, and its own docstring says so.
        self.set_resizable(False)
        self.add_css_class("nabria-setup")
        install_style(settings)

        self.has_gpu = gpu.plan("auto").use_gpu

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_margin_top(24)
        self.stack.set_margin_bottom(24)
        self.stack.set_margin_start(24)
        self.stack.set_margin_end(24)
        self.set_child(self.stack)

        self.stack.add_named(self._welcome_page(), "welcome")
        self.stack.add_named(self._language_page(), "language")
        self.stack.add_named(self._model_page(), "model")
        self.stack.add_named(self._download_page(), "download")
        self.stack.add_named(self._microphone_page(), "microphone")
        self.stack.add_named(self._shortcut_page(), "shortcut")
        self.stack.set_visible_child_name("welcome")

    # -- scaffolding -------------------------------------------------------

    def _page(self, title: str, lede: str) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        heading = i18n.label(title)
        heading.add_css_class("nabria-title")
        page.append(heading)
        if lede:
            subtitle = i18n.label(lede, wrap=True)
            subtitle.add_css_class("nabria-lede")
            page.append(subtitle)
        return page

    def _buttons(self, page: Gtk.Box, *buttons: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.END)
        row.set_margin_top(8)
        for button in buttons:
            row.append(button)
        page.append(row)

    # -- pages -------------------------------------------------------------

    def _welcome_page(self) -> Gtk.Box:
        page = self._page(
            i18n.t("wizard.welcome.title"), i18n.t("wizard.welcome.lede")
        )
        start = Gtk.Button(label=i18n.t("wizard.welcome.button"))
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda _b: self.stack.set_visible_child_name("language"))
        self._buttons(page, start)
        return page

    def _language_page(self) -> Gtk.Box:
        page = self._page(
            i18n.t("wizard.language.title"), i18n.t("wizard.language.lede")
        )

        # Preselect the language the interface came up in: it was resolved
        # from the desktop's locale, and someone whose system is already in
        # Arabic should not have to say so twice.
        preferred = i18n.current()

        cards = []
        self.languages: list[tuple[str, Gtk.CheckButton]] = []
        for code, preset in config.LANGUAGE_PRESETS.items():
            card = Choice(i18n.t(preset["label"]), i18n.t(preset["summary"]))
            cards.append(card)
            self.languages.append((code, card.radio))
            page.append(card)
        group(cards)
        for code, radio in self.languages:
            radio.set_active(code == preferred)

        onward = Gtk.Button(label=i18n.t("wizard.next"))
        onward.add_css_class("suggested-action")
        onward.connect("clicked", lambda _b: self._choose_language())
        self._buttons(page, onward)
        return page

    def _choose_language(self) -> None:
        code = next(
            (code for code, radio in self.languages if radio.get_active()), "auto"
        )
        preset = config.LANGUAGE_PRESETS[code]
        self.settings["language"] = code
        # The dialect prompt is the whole reason Arabic works well here, and
        # nobody would ever find it in a config file. Setting it only when it
        # is empty means this never overwrites something the user wrote.
        if preset["vocabulary"] and not str(self.settings.get("vocabulary") or "").strip():
            self.settings["vocabulary"] = preset["vocabulary"]
        config.save(self.settings)
        self.stack.set_visible_child_name("model")

    def _model_page(self) -> Gtk.Box:
        recommended = models.recommended(self.has_gpu)
        page = self._page(
            i18n.t("wizard.model.title"),
            i18n.t("wizard.model.lede_gpu" if self.has_gpu else "wizard.model.lede_nogpu"),
        )

        self.choices = []
        for model in models.CATALOG.values():
            card = Choice(
                # The model's own name is never translated: it is what the
                # file is called and what every whisper.cpp page calls it, so
                # a translated one could not be looked up anywhere.
                i18n.ltr(model.key), i18n.t(model.summary),
                trailing=i18n.t("wizard.model.size", megabytes=model.megabytes),
                badge=i18n.t("wizard.model.recommended")
                      if model.key == recommended.key else "",
                note=i18n.t("wizard.model.needs_gpu")
                     if model.needs_gpu and not self.has_gpu else "",
            )
            card.model = model
            self.choices.append(card)
            page.append(card)
        group(self.choices)
        for card in self.choices:
            card.radio.set_active(card.model.key == recommended.key)

        fetch = Gtk.Button(label=i18n.t("wizard.download"))
        fetch.add_css_class("suggested-action")
        fetch.connect("clicked", lambda _b: self._begin_download())
        self._buttons(page, fetch)
        return page

    def _download_page(self) -> Gtk.Box:
        page = self._page(i18n.t("wizard.downloading"), "")
        self.progress = Gtk.ProgressBar(show_text=True)
        page.append(self.progress)
        self.download_note = i18n.label("", wrap=True)
        self.download_note.add_css_class("nabria-hint")
        page.append(self.download_note)

        self.download_next = Gtk.Button(label=i18n.t("wizard.next"))
        self.download_next.add_css_class("suggested-action")
        self.download_next.set_sensitive(False)
        self.download_next.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("microphone")
        )
        self.download_retry = Gtk.Button(label=i18n.t("wizard.try_again"))
        self.download_retry.set_visible(False)
        self.download_retry.connect("clicked", lambda _b: self._begin_download())
        self._buttons(page, self.download_retry, self.download_next)
        return page

    def _microphone_page(self) -> Gtk.Box:
        page = self._page(i18n.t("wizard.mic.title"), i18n.t("wizard.mic.lede"))
        self.mic_result = i18n.label("", wrap=True)
        page.append(self.mic_result)

        test = Gtk.Button(label=i18n.t("wizard.mic.test"))
        test.connect("clicked", lambda _b: self._test_microphone())
        skip = Gtk.Button(label=i18n.t("wizard.mic.skip"))
        skip.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("shortcut")
        )
        self._buttons(page, skip, test)
        return page

    def _shortcut_page(self) -> Gtk.Box:
        page = self._page(
            i18n.t("wizard.shortcut.title"), i18n.t("wizard.shortcut.lede")
        )
        # A sentence, then the lines to paste -- the shape `instructions()`
        # documents, unpacked rather than rediscovered with index arithmetic.
        sentence, *commands = shortcut.instructions()
        page.append(_key_line(sentence))
        for command in commands:
            page.append(_key_line(command, pasteable=True))

        self.bind_result = i18n.label("", wrap=True)
        page.append(self.bind_result)

        done = Gtk.Button(label=i18n.t("wizard.done"))
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda _b: self._finish())

        # Offered only where the answer is a file that can be appended to. On
        # KDE and GNOME it is a settings dialog, and on niri the lines belong
        # inside a block -- there is nothing honest for a button to do on any
        # of the three, and one that failed would be worse than none.
        if shortcut.config_file() is not None:
            add = Gtk.Button(label=i18n.t("wizard.shortcut.bind"))
            add.connect("clicked", self._bind_shortcut)
            self._buttons(page, add, done)
        else:
            self._buttons(page, done)
        return page

    def _bind_shortcut(self, button: Gtk.Button) -> None:
        """Write the lines into the compositor's configuration file."""
        path = shortcut.config_file()
        assert path is not None  # the button only exists when there is one

        for name in ("nabria-good", "nabria-bad"):
            self.bind_result.remove_css_class(name)

        if shortcut.already_bound(path):
            # Not an error, and not a reason to write it twice.
            self.bind_result.set_text(
                i18n.t("wizard.shortcut.already", path=i18n.ltr(path))
            )
            button.set_sensitive(False)
            return
        try:
            written = shortcut.bind(path)
        except OSError as exc:
            # Safe to leave the button live: the write is atomic, so a failure
            # means the file is exactly as it was and trying again is the
            # right thing to offer.
            self.bind_result.add_css_class("nabria-bad")
            self.bind_result.set_text(
                i18n.t("wizard.shortcut.failed", path=i18n.ltr(path),
                       error=i18n.ltr(exc))
            )
            return
        self.bind_result.add_css_class("nabria-good")
        self.bind_result.set_text(
            i18n.t("wizard.shortcut.bound", path=i18n.ltr(written))
            + " " + i18n.t("wizard.shortcut.reload")
        )
        button.set_sensitive(False)

    # -- actions -----------------------------------------------------------

    def _selected_model(self) -> models.Model:
        for choice in self.choices:
            if choice.radio.get_active():
                return choice.model
        return models.recommended(self.has_gpu)

    def _begin_download(self) -> None:
        model = self._selected_model()
        if models.installed(config.MODEL_DIR, model):
            # Already fetched, almost always by install.sh. Showing a download
            # page that completes instantly reads as a glitch.
            self.settings["model"] = str(config.MODEL_DIR / model.filename)
            config.save(self.settings)
            self.stack.set_visible_child_name("microphone")
            return
        self.stack.set_visible_child_name("download")
        self.download_next.set_sensitive(False)
        self.download_retry.set_visible(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text(
            i18n.t("wizard.progress", model=i18n.ltr(model.key),
                   done=0, total=model.megabytes)
        )
        self.download_note.set_text("")

        def report(done: int, total: int) -> None:
            GLib.idle_add(self._show_progress, model, done, total)

        def work() -> None:
            try:
                path = models.download(model, config.MODEL_DIR, report)
            except models.DownloadError as exc:
                GLib.idle_add(self._download_failed, str(exc))
                return
            GLib.idle_add(self._download_done, model, path)

        threading.Thread(target=work, daemon=True, name="nabria-fetch").start()

    def _show_progress(self, model, done: int, total: int) -> bool:
        self.progress.set_fraction(done / max(total, 1))
        self.progress.set_text(i18n.t(
            "wizard.progress", model=i18n.ltr(model.key),
            done=done // 1_000_000, total=total // 1_000_000,
        ))
        return GLib.SOURCE_REMOVE

    def _download_done(self, model, path) -> bool:
        self.progress.set_fraction(1.0)
        self.progress.set_text(i18n.t("wizard.verified"))
        self.download_note.set_text(i18n.ltr(path))
        self.settings["model"] = str(path)
        config.save(self.settings)
        self.download_next.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _download_failed(self, message: str) -> bool:
        self.progress.set_text(i18n.t("wizard.failed"))
        self.download_note.set_text(message)
        self.download_retry.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _test_microphone(self) -> None:
        self.mic_result.set_text(i18n.t("wizard.mic.listening"))

        def work() -> None:
            try:
                level = audio.measure(4.0)
            except audio.AudioError as exc:
                GLib.idle_add(self._microphone_result, None, str(exc))
                return
            GLib.idle_add(self._microphone_result, level, "")

        threading.Thread(target=work, daemon=True, name="nabria-mic-test").start()

    def _microphone_result(self, level, error: str) -> bool:
        for name in ("nabria-good", "nabria-bad"):
            self.mic_result.remove_css_class(name)
        if error:
            self.mic_result.add_css_class("nabria-bad")
            self.mic_result.set_text(error)
        elif level is not None and level > GOOD_ENOUGH_DBFS:
            self.mic_result.add_css_class("nabria-good")
            self.mic_result.set_text(
                i18n.t("wizard.mic.heard", level=i18n.ltr(f"{level:.0f}"))
            )
            self.stack.set_visible_child_name("shortcut")
        else:
            self.mic_result.add_css_class("nabria-bad")
            self.mic_result.set_text(
                i18n.t("wizard.mic.barely", level=i18n.ltr(f"{level:.0f}"))
            )
        return GLib.SOURCE_REMOVE

    def _finish(self) -> None:
        self.settings["setup_done"] = True
        config.save(self.settings)
        self.close()
        self.on_finished()
