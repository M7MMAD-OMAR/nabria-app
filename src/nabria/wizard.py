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
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import audio, config, gpu, i18n, models, shortcut, theme  # noqa: E402

# The window's width, and the only dimension of it that is fixed. The pages
# differ in length and not in measure, so a width that moves between steps
# would be motion carrying no information.
WIDTH = 560
# Tall enough for the longest step -- the model list -- with the shorter ones
# filling the difference rather than leaving it blank.
HEIGHT = 560

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
                 note_style: str = "nabria-bad", badge: str = "",
                 trailing: str = ""):
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

        # Reserved for the thing that turns a preference into a mistake --
        # which is why it is red by default, and why `note_style` exists for
        # the notes that are merely worth knowing. A caution that always looks
        # like a failure stops being read as either.
        if note:
            warning = i18n.label(note, wrap=True)
            warning.add_css_class(note_style)
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


def choose_model_file(parent: Gtk.Window, on_chosen) -> None:
    """Ask for a model file, on either of the toolkit's two file dialogs.

    `Gtk.FileDialog` arrived in GTK 4.10 and Debian stable ships 4.8, which is
    inside the distribution matrix -- so the older `FileChooserNative` is not a
    courtesy to old systems, it is the only one that opens on one of the three
    this program is tested against.

    Dismissing the dialog is not an error and says nothing: somebody who
    changed their mind is told nothing, because there is nothing to tell them.
    Choosing a file that is not on this machine *is* something, and is reported
    -- `on_chosen(None)` -- rather than closing the dialog and doing nothing.
    """
    only_models = Gtk.FileFilter()
    only_models.set_name("*.bin")
    only_models.add_pattern("*.bin")

    def chosen(file) -> None:
        if file is None:
            return
        # A file picked over sftp or smb has no local path at all. Reading it
        # would mean copying gigabytes over somebody's network to a directory
        # that must hold the real thing, so it is refused and said out loud.
        path = file.get_path()
        on_chosen(Path(path) if path else None)

    if hasattr(Gtk, "FileDialog"):
        dialog = Gtk.FileDialog(title=i18n.t("wizard.model.choose"))
        dialog.set_default_filter(only_models)

        def finished(source, result) -> None:
            try:
                chosen(source.open_finish(result))
            except GLib.Error:
                pass  # dismissed

        dialog.open(parent, None, finished)
        return

    chooser = Gtk.FileChooserNative(
        title=i18n.t("wizard.model.choose"),
        transient_for=parent,
        action=Gtk.FileChooserAction.OPEN,
    )
    # `Gtk.FileDialog` is modal by default and `Gtk.NativeDialog` is not, so
    # without this the two branches behave differently in the way that matters:
    # the wizard stays clickable behind the older chooser, and a second click
    # on the same button would drop the only reference to the first one while
    # it is still on the screen. The same click could also start the download
    # and leave the chooser editing a page that had moved on.
    chooser.set_modal(True)
    chooser.add_filter(only_models)

    def responded(dialog, response) -> None:
        file = dialog.get_file()
        dialog.destroy()
        parent._chooser = None
        if response == Gtk.ResponseType.ACCEPT:
            chosen(file)

    chooser.connect("response", responded)
    # Held on the window on purpose: a native chooser that nothing else
    # references is collected while it is still on the screen.
    parent._chooser = chooser
    chooser.show()


def _status(label: Gtk.Label, text: str = "", *, style: str = "") -> None:
    """Show a status line, or take its space back when there is nothing to say.

    An empty `Gtk.Label` still occupies a line. Four of these sit between a
    page's content and its buttons, holding room for a message that has not
    happened yet -- and once the window began sizing itself to its content,
    that reserved room stopped being invisible and became a gap in the middle
    of the page, which is what it had always been.

    It also clears the two result colours every time, so a failure followed by
    a success is not printed in red.
    """
    for name in ("nabria-good", "nabria-bad"):
        label.remove_css_class(name)
    if style:
        label.add_css_class(style)
    label.set_text(text)
    label.set_visible(bool(text))


def group(cards: list[Choice]) -> None:
    """Make a list of cards one radio group."""
    for card in cards[1:]:
        card.radio.set_group(cards[0].radio)


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, settings: dict, on_finished):
        super().__init__(application=application, title="Nabria")
        self.settings = settings
        self.on_finished = on_finished
        # One frame for all five steps, and the buttons anchored to the
        # bottom of it.
        #
        # A window that resized itself to each page was tried and measured, and
        # it does not survive contact with a compositor: asking for 560 wide
        # and leaving the height open came back 1217x232, and setting both on a
        # window already on screen was honoured on three pages out of six.
        # `set_default_size` is read when the window is mapped, and a
        # `set_size_request` that would force the issue is a *minimum*, so the
        # window could then never shrink again.
        #
        # So the frame is fixed and the layout fills it instead. The empty
        # space under the welcome step was never the fixed height -- it was the
        # buttons floating in the middle of it.
        self.set_default_size(WIDTH, HEIGHT)
        # Five fixed steps with nothing to resize and nothing to keep open, so
        # it asks to be a fixed panel rather than a window. Whether it gets
        # one is the compositor's decision; measured, Hyprland honours the
        # hint and floats it. Where a compositor does not, the README gives the
        # window rule -- keyed on the title, which is the product name and is
        # never translated.
        #
        # The settings window deliberately does *not* do this: it has a
        # scrollable transcript list, which is the one place resizing earns
        # its keep, and its own docstring says so.
        self.set_resizable(False)
        self.add_css_class("nabria-setup")
        install_style(settings)

        self.has_gpu = gpu.plan("auto").use_gpu

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        # The stack stays vertically homogeneous, which is its default and is
        # load-bearing in a way that is not obvious. Turning it off -- so each
        # page would ask for its own height -- also switches the width request
        # onto the height-for-width path, and there a wrapping paragraph asks
        # for its unwrapped width. Measured on the same window, same content,
        # one property apart: 560x560 with it on, 1217x560 with it off.
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
        # Slack above the step as well as below it, so a short one sits in the
        # middle of the frame rather than clinging to the top of it. On the
        # steps that fill the window there is no slack and this does nothing;
        # on the welcome step it is the difference between a title card and a
        # page that looks like it stopped loading.
        page.append(Gtk.Box(vexpand=True))

        heading = i18n.label(title)
        heading.add_css_class("nabria-title")
        # Kept on the box so a page can retitle itself without going hunting
        # through its own children. The download page needs it: the same page
        # is a download and a checksum, and calling the second one a download
        # would be a lie in the largest text on the screen.
        page.heading = heading
        page.append(heading)
        if lede:
            subtitle = i18n.label(lede, wrap=True)
            subtitle.add_css_class("nabria-lede")
            page.append(subtitle)
        return page

    def _buttons(self, page: Gtk.Box, *buttons: Gtk.Widget) -> None:
        # Whatever is left over goes above the buttons, not below them. A row
        # that sits directly under two sentences, with a third of the window
        # empty beneath it, reads as a page that failed to finish loading --
        # which is exactly how the welcome step looked.
        page.append(Gtk.Box(vexpand=True))

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

        # The cards live in their own box so that one picked by hand later can
        # be appended without landing underneath the buttons -- and inside a
        # scroller, because this is the one page whose length is not known when
        # it is written. Three cards is the floor; a machine holding several
        # models already, plus anything picked by hand, has as many as it has.
        # The window asks not to be resizable, so without this the buttons
        # would be pushed off the bottom of a window that cannot be made
        # taller, and the page would be unusable at exactly the moment the
        # search had worked best.
        self.model_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Natural height while everything fits, so three cards do not sit in a
        # tall empty box on the ordinary first run -- and a ceiling, because
        # the window sizes itself to its content now, so an uncapped list would
        # grow the window past the screen instead of scrolling.
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(430)
        scroller.set_child(self.model_list)
        page.append(scroller)

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
            card.found = None
            self.choices.append(card)
            self.model_list.append(card)

        # Look before offering to download. Most of these are hundreds of
        # megabytes and a great many machines already hold one, from a
        # checkout, another program, or a browser -- and the search costs a
        # handful of directory listings and four bytes per candidate, so it
        # runs while the page is being built rather than behind a button
        # somebody has to know to press.
        for entry in models.search(model_dir=config.MODEL_DIR):
            self.choices.append(self._found_card(entry))

        group(self.choices)
        default = self._default_choice(recommended)
        for card in self.choices:
            card.radio.set_active(card is default)

        self.model_note = i18n.label("", wrap=True, visible=False)
        self.model_note.add_css_class("nabria-hint")
        page.append(self.model_note)

        self.fetch = Gtk.Button(label=i18n.t("wizard.download"))
        self.fetch.add_css_class("suggested-action")
        self.fetch.connect("clicked", lambda _b: self._begin_download())
        # The other half of the answer: a model that is somewhere this search
        # has no business looking, which is most places a person might keep
        # one.
        browse = Gtk.Button(label=i18n.t("wizard.model.choose"))
        browse.connect("clicked", lambda _b: choose_model_file(self, self._offer_file))
        self._buttons(page, browse, self.fetch)

        # Connected after the button exists, because the handler renames it.
        for card in self.choices:
            card.radio.connect("toggled", self._retitle_action)
        self._retitle_action()
        return page

    def _found_card(self, entry: models.Found) -> Choice:
        """One card for a model that is already here, appended to the list."""
        card = Choice(
            i18n.ltr(entry.name),
            # The path, not a summary: which of the copies on this machine
            # this is, is the only thing the reader needs to decide.
            i18n.ltr(entry.path),
            trailing=i18n.t("wizard.model.here"),
            note="" if entry.model else i18n.t("wizard.model.unverifiable"),
            # Worth knowing, not a failure. The red on the other cards means
            # "this will not keep up with speech on this machine"; a file with
            # no published copy to compare against still works.
            note_style="nabria-lede",
        )
        card.model = entry.model
        card.found = entry
        self.model_list.append(card)
        return card

    def _default_choice(self, recommended: models.Model) -> Choice:
        """Which card starts selected.

        A model already on the disk wins over an equal or worse one behind a
        download -- that is the whole feature, and having to notice a card and
        click it is barely better than not having one.

        It does not win a *downgrade*, which is the part that is easy to get
        wrong. Finding the smallest model on a machine with a graphics card is
        not a reason to install the smallest model: it saves one download,
        once, and costs accuracy on every sentence afterwards. So a found model
        is preselected only when it is at least the size of the one recommended
        for this machine, and the recommendation keeps the page otherwise --
        with the found card still sitting there for anyone who would rather
        skip the download.

        "Can run" is the same judgement `best_installed` makes: a model wanting
        a graphics card this machine does not have is not a slower answer, it
        is an unusable one, and choosing it *for* somebody would be this
        program's mistake rather than their preference.

        An unrecognised file is never chosen for anyone either. There is no
        published copy to check it against, so taking it has to be a decision
        somebody made.
        """
        usable = [
            card for card in self.choices
            if card.found is not None and card.model is not None
            and (self.has_gpu or not card.model.needs_gpu)
            and card.model.size >= recommended.size
        ]
        if usable:
            return max(usable, key=lambda card: card.model.size)
        return next(
            card for card in self.choices
            if card.found is None and card.model.key == recommended.key
        )

    def _selected_choice(self) -> Choice:
        for card in self.choices:
            if card.radio.get_active():
                return card
        return self.choices[0]

    def _retitle_action(self, *_args) -> None:
        """Say what the button is about to do, which is not always a download.

        A model already on the machine -- found, hand-picked, or fetched by
        install.sh before the wizard ever opened -- needs no download at all,
        and a button still saying "Download" above a card saying "already on
        this machine" reads as the search not having worked.
        """
        card = self._selected_choice()
        here = card.found is not None or (
            card.model is not None and models.installed(config.MODEL_DIR, card.model)
        )
        self.fetch.set_label(i18n.t("wizard.model.use" if here else "wizard.download"))

    def _offer_file(self, path: Path | None) -> None:
        """Add a hand-picked file to the page, chosen, or say why it cannot be."""
        if path is None:
            # Picked over a network mount, so there is no file here to link to.
            _status(self.model_note, i18n.t("wizard.model.not_local"),
                    style="nabria-bad")
            return
        if not models.looks_like_a_model(path):
            _status(self.model_note,
                    i18n.t("wizard.model.not_a_model", path=i18n.ltr(path)),
                    style="nabria-bad")
            return
        _status(self.model_note)

        for card in self.choices:
            if card.found is not None and card.found.path == path:
                card.radio.set_active(True)  # already listed; just choose it
                return

        card = self._found_card(models.Found(path, models.identify(path)))
        card.radio.set_group(self.choices[0].radio)
        card.radio.connect("toggled", self._retitle_action)
        self.choices.append(card)
        card.radio.set_active(True)

    def _download_page(self) -> Gtk.Box:
        page = self._page(i18n.t("wizard.downloading"), "")
        self.download_title = page.heading
        self.progress = Gtk.ProgressBar(show_text=True)
        page.append(self.progress)
        self.download_note = i18n.label("", wrap=True, visible=False)
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
        self.mic_result = i18n.label("", wrap=True, visible=False)
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

        self.bind_result = i18n.label("", wrap=True, visible=False)
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

        if shortcut.already_bound(path):
            # Not an error, and not a reason to write it twice.
            _status(self.bind_result,
                    i18n.t("wizard.shortcut.already", path=i18n.ltr(path)))
            button.set_sensitive(False)
            return
        try:
            written = shortcut.bind(path)
        except OSError as exc:
            # Safe to leave the button live: the write is atomic, so a failure
            # means the file is exactly as it was and trying again is the
            # right thing to offer.
            _status(self.bind_result,
                    i18n.t("wizard.shortcut.failed", path=i18n.ltr(path),
                           error=i18n.ltr(exc)),
                    style="nabria-bad")
            return
        _status(self.bind_result,
                i18n.t("wizard.shortcut.bound", path=i18n.ltr(written))
                + " " + i18n.t("wizard.shortcut.reload"),
                style="nabria-good")
        button.set_sensitive(False)

    # -- actions -----------------------------------------------------------

    def _begin_download(self) -> None:
        card = self._selected_choice()
        if card.found is not None:
            self._adopt(card.found)
            return
        model = card.model
        if models.installed(config.MODEL_DIR, model):
            # Already fetched, almost always by install.sh. Showing a download
            # page that completes instantly reads as a glitch.
            self.settings["model"] = str(config.MODEL_DIR / model.filename)
            config.save(self.settings)
            self.stack.set_visible_child_name("microphone")
            return
        self._begin_work(i18n.t("wizard.downloading"))
        self.progress.set_text(
            i18n.t("wizard.progress", model=i18n.ltr(model.key),
                   done=0, total=model.megabytes)
        )
        _status(self.download_note)

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

    def _begin_work(self, title: str) -> None:
        """Put the progress page up, named for what is about to happen on it."""
        self.stack.set_visible_child_name("download")
        self.download_title.set_text(title)
        self.download_next.set_sensitive(False)
        self.download_retry.set_visible(False)
        self.progress.set_fraction(0.0)

    def _adopt(self, entry: models.Found) -> None:
        """Link a model that is already on this machine into place.

        On the progress page, and not instantly, because the check that makes
        it safe reads the whole file: up to 1.6 GB of sha256, which needs
        somewhere to show that it is moving or it reads as a hang. A recognised
        file is held to exactly the standard a downloaded one is. An
        unrecognised one has no published copy to be compared against, so there
        is nothing to read and the page passes straight through -- which is
        also why its card says so before this point is reached.
        """
        self._begin_work(i18n.t("wizard.checking"))
        self.progress.set_text(i18n.t("wizard.checking"))
        _status(self.download_note, i18n.ltr(entry.path))

        def report(done: int, total: int) -> None:
            GLib.idle_add(self._show_progress, entry.model, done, total)

        def work() -> None:
            try:
                path = models.adopt(
                    entry.path, config.MODEL_DIR, entry.model, report
                )
            except models.DownloadError as exc:
                GLib.idle_add(self._download_failed, str(exc))
                return
            GLib.idle_add(self._download_done, entry.model, path)

        threading.Thread(target=work, daemon=True, name="nabria-adopt").start()

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
        _status(self.download_note, i18n.ltr(path))
        self.settings["model"] = str(path)
        config.save(self.settings)
        self.download_next.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _download_failed(self, message: str) -> bool:
        self.progress.set_text(i18n.t("wizard.failed"))
        _status(self.download_note, message)
        self.download_retry.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _test_microphone(self) -> None:
        _status(self.mic_result, i18n.t("wizard.mic.listening"))

        def work() -> None:
            try:
                level = audio.measure(4.0)
            except audio.AudioError as exc:
                GLib.idle_add(self._microphone_result, None, str(exc))
                return
            GLib.idle_add(self._microphone_result, level, "")

        threading.Thread(target=work, daemon=True, name="nabria-mic-test").start()

    def _microphone_result(self, level, error: str) -> bool:
        if error:
            _status(self.mic_result, error, style="nabria-bad")
        elif level is not None and level > GOOD_ENOUGH_DBFS:
            _status(self.mic_result,
                    i18n.t("wizard.mic.heard", level=i18n.ltr(f"{level:.0f}")),
                    style="nabria-good")
            self.stack.set_visible_child_name("shortcut")
        else:
            _status(self.mic_result,
                    i18n.t("wizard.mic.barely", level=i18n.ltr(f"{level:.0f}")),
                    style="nabria-bad")
        return GLib.SOURCE_REMOVE

    def _finish(self) -> None:
        self.settings["setup_done"] = True
        config.save(self.settings)
        self.close()
        self.on_finished()
