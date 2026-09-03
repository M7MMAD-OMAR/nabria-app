"""Every user-facing string, in English and Arabic.

A plain dict rather than gettext. gettext would mean compiled `.mo` files,
which means new lines in `packaging/layout.sh`, a new assertion in the package
matrix and a new packaging test -- reopening a layout that is proved on three
distributions -- to hold sixty strings in two languages. The dict is already
covered by the `install -m 644 src/nabria/*.py` that ships every other module.

Two languages that are not the same setting:

  `language`     what you *speak*. Passed to whisper.
  `ui_language`  what the app is *written in*. Nothing to do with the engine.

Someone dictating Arabic into an English-language desktop is ordinary, and so
is the reverse, so tying one to the other would be wrong for both of them.

**Embedded Latin in an Arabic sentence must be isolated** -- `ltr()`. Without
it, a device name, a file path or a figure like "-42 dBFS" is reordered by the
bidirectional algorithm against the surrounding right-to-left run, and comes
out scrambled: the minus sign migrates, and "Ctrl+V" can render as "V+Ctrl".
Setting the paragraph direction does not fix this and never did; the isolate
characters are the fix.
"""

from __future__ import annotations

import os

# First-strong isolate, and its terminator. Everything between them is laid out
# on its own, then placed into the surrounding text as a single neutral object.
_FSI = "⁨"
_PDI = "⁩"

LANGUAGES = ("en", "ar")
RTL = {"ar"}

_current = "en"


def ltr(text: object) -> str:
    """Isolate a run of Latin text so the surrounding Arabic does not reorder it.

    Applied to anything that comes from outside the string table -- paths,
    device names, engine errors, numbers with units, key names. Harmless in
    English: the isolate characters are zero-width and the layout is unchanged.
    """
    return f"{_FSI}{text}{_PDI}"


def resolve(setting: str) -> str:
    """`auto` means the desktop's language; anything else is taken as asked.

    Only the two-letter prefix is read, so `ar_SY.UTF-8` and `ar` are the same
    answer, and an unknown locale falls back to English rather than to a
    half-translated screen.
    """
    if setting in LANGUAGES:
        return setting
    locale = (
        os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG") or ""
    ).lower()
    return next((code for code in LANGUAGES if locale.startswith(code)), "en")


def use(setting: str) -> str:
    """Select the language for everything that follows. Returns what was chosen."""
    global _current
    _current = resolve(setting)
    return _current


def apply(setting: str) -> str:
    """`use()`, and set GTK's default text direction to match it.

    The two always go together -- an Arabic interface laid out left to right is
    a half-translated one -- and they were written out separately at each of
    the three places that select a language, one of which set RTL and never
    set it back.

    GTK is imported here rather than at the top of the module because
    `shortcut.py` imports this one, and `__main__.py` dispatches every control
    command without ever importing GTK. That is what makes `nabria toggle`
    cost a socket write instead of a toolkit.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    code = use(setting)
    Gtk.Widget.set_default_direction(
        Gtk.TextDirection.RTL if is_rtl() else Gtk.TextDirection.LTR
    )
    return code


def current() -> str:
    return _current


def is_rtl() -> bool:
    return _current in RTL


def start_align() -> float:
    """`xalign` for text that should hug the side the reader starts from.

    GTK's `xalign` is absolute -- 0.0 is the left edge whatever the paragraph
    direction -- so a hardcoded 0.0 pins Arabic to the left of its own window.
    `Gtk.Align.START` is direction-aware and is used where the widget is being
    aligned; this is for the label's text inside its own allocation, which is a
    different thing and has no direction-aware form.
    """
    return 1.0 if is_rtl() else 0.0


def label(text: str = "", **properties: object):
    """A `Gtk.Label` that starts on the side the reader starts from.

    The rule this exists for -- never `xalign=0` -- was enforced by memory
    across fourteen call sites, and its failure mode is a *new* label written
    the obvious way, which looks correct until Arabic is selected and then pins
    a sentence to the wrong edge of its own window. A default is checkable; a
    convention in a document is not.

    GTK is imported inside, for the reason given on `apply()`.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    properties.setdefault("xalign", start_align())
    # A wrapping label reports the width of the *unwrapped* sentence as its
    # natural width, which is the whole paragraph on one line. That is only a
    # preference in a window the user can resize; in one that sizes itself to
    # its content it is the width, and the setup window came out sixteen
    # hundred pixels wide with three lines of text strung across it.
    #
    # `max_width_chars = 1` is the GTK idiom for "wrap to whatever I am given"
    # -- it collapses the natural width to the minimum, so the container's own
    # width decides and the text wraps inside it.
    if properties.get("wrap"):
        properties.setdefault("max_width_chars", 1)
    return Gtk.Label(label=text, **properties)


def t(key: str, **fields: object) -> str:
    """Look up a string and fill in its fields.

    An unknown key returns the key. That is deliberately not an exception: a
    missing translation must degrade to something legible in the interface, not
    take down the window that was being built. It is also what lets a caller
    pass a literal through -- `tests/test_models.py` builds a Model with an
    empty summary, and `t("")` has to be harmless.
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    # `format_map`, always, and never a "no fields so skip it" branch. That
    # branch is why `shortcut.niri` rendered its escaped `{{}}` literally: a
    # string written for one path was taken down the other. `_Placeholders`
    # makes a forgotten field render as `{path}` rather than raise inside the
    # widget builder that was constructing a window.
    #
    # Both languages are guaranteed present by test_i18n, so this indexes
    # rather than falling back.
    return entry[_current].format_map(_Placeholders(fields))


class _Placeholders(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# --------------------------------------------------------------------------
# The strings.
#
# Keys are `surface.thing`. Arabic here is Modern Standard, not dialect: this
# is interface text read by everyone, unlike the dictation prompt in config.py
# which is deliberately Levantine because it is steering a speech model.
#
# `{}` fields carrying Latin text, a path, or a number with a sign or a unit
# attached are `ltr()`-wrapped by the caller. A field that is only digits is
# not, and must not be: digits already take their direction from the text
# around them, so "148 م.ب" comes out in that order, while an isolate would
# make the number a neutral object and move it to the far side of the unit.
# --------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    # -- notifications from the daemon -------------------------------------
    "app.cannot_record": {
        "en": "Cannot record",
        "ar": "تعذّر التسجيل",
    },
    "app.dictation_error": {
        "en": "Dictation error",
        "ar": "خطأ في الإملاء",
    },
    "app.transcribe_failed": {
        "en": "Transcription failed",
        "ar": "فشل التفريغ",
    },
    "app.type_failed": {
        "en": "Could not type the text",
        "ar": "تعذّرت كتابة النص",
    },
    "app.type_failed_body": {
        "en": "The text is on the clipboard, paste it with {key}",
        "ar": "النص في الحافظة، الصقه بـ {key}",
    },
    "app.not_hearing": {
        "en": "Dictation is not hearing the microphone",
        "ar": "الإملاء لا يسمع الميكروفون",
    },
    "app.not_hearing_body": {
        "en": "{takes} takes in a row with nothing above {threshold} dBFS — "
              "the last was {level} dBFS from {source}. Check the input device.",
        "ar": "{takes} تسجيلات متتالية لم يتجاوز أيٌّ منها {threshold} ديسيبل — "
              "آخرها {level} ديسيبل من {source}. تحقّق من جهاز الإدخال.",
    },
    # Said while the take is still running, which is the only time it can
    # still save the sentence being spoken.
    "app.unheard": {
        "en": "Still recording, but hearing nothing",
        "ar": "التسجيل مستمر، لكن لا يصل أي صوت",
    },
    "app.unheard_muted_body": {
        "en": "{source} is muted. Unmute it and say that again: nothing so far "
              "has been recorded.",
        "ar": "الميكروفون {source} مكتوم. أعد تفعيله وأعد ما قلته، فلم يُسجَّل شيء "
              "حتى الآن.",
    },
    "app.unheard_body": {
        "en": "{seconds} seconds in and nothing has risen above {threshold} dBFS "
              "on {source}. Check that it is not muted, and that the right "
              "input is selected.",
        "ar": "مضت {seconds} ثانية دون أن يتجاوز أي صوت {threshold} ديسيبل على "
              "{source}. تأكّد أنه غير مكتوم وأن جهاز الإدخال المختار صحيح.",
    },
    # The take was kept, so this is not a failure. It is the one warning that
    # dictation is now slower than it should be, which is otherwise invisible.
    "app.gpu_fallback": {
        "en": "Dictation is running on the CPU",
        "ar": "الإملاء يعمل على المعالج",
    },
    "app.gpu_fallback_body": {
        "en": "The graphics card could not be used, so transcription is slower "
              "than usual. Your words were kept. Restart Nabria to try the "
              "card again; the reason is in {log}.",
        "ar": "تعذّر استخدام بطاقة الرسوميات، فصار التفريغ أبطأ من المعتاد. لم "
              "يضِع كلامك. أعد تشغيل نبرة لتجربة البطاقة من جديد، والسبب "
              "مذكور في {log}.",
    },

    # -- the model catalogue -----------------------------------------------
    "model.base.summary": {
        "en": "Fast on any machine. Good enough for clear speech.",
        "ar": "سريع على أي جهاز. يكفي للكلام الواضح.",
    },
    "model.small.summary": {
        "en": "Noticeably better, still comfortable without a GPU.",
        "ar": "أدقّ بوضوح، ولا يزال مريحاً دون بطاقة رسوميات.",
    },
    "model.large-v3-turbo.summary": {
        "en": "The best, and the one to use for Arabic. Wants a discrete GPU.",
        "ar": "الأفضل، وهو المناسب للعربية. يحتاج بطاقة رسوميات منفصلة.",
    },

    # -- the spoken-language presets ---------------------------------------
    #
    # A language names itself in its own script in both translations: someone
    # looking for their language finds it by recognising it, and translating
    # "Arabic" into Arabic hides it from the person who reads no English.
    "language.ar.label": {"en": "العربية", "ar": "العربية"},
    "language.en.label": {"en": "English", "ar": "English"},
    "language.auto.label": {
        "en": "Work it out",
        "ar": "اكتشفها تلقائياً",
    },
    "language.ar.summary": {
        "en": "Arabic, including spoken dialect. Ships a Levantine prompt.",
        "ar": "العربية، بما فيها المحكية. مع تلميح شامي جاهز.",
    },
    "language.auto.summary": {
        "en": "Detected per phrase. Least accurate, and it can turn room "
              "noise into confident nonsense in another language.",
        "ar": "يُكتشف لكل عبارة. الأقل دقة، وقد يحوّل ضجيج الغرفة إلى كلام "
              "واثق لا معنى له بلغة أخرى.",
    },

    # -- the setup wizard ---------------------------------------------------
    "wizard.welcome.title": {
        "en": "Just talk.",
        "ar": "تكلّم فحسب.",
    },
    "wizard.welcome.lede": {
        "en": "Press a key, say what you mean, press it again. The words "
              "appear in whatever you were typing into.\n\n"
              "Everything happens on this machine. No account, nothing uploaded.",
        "ar": "اضغط مفتاحاً، قل ما تريد، ثم اضغطه ثانية. تظهر الكلمات في "
              "المكان الذي كنت تكتب فيه.\n\n"
              "كل شيء يجري على هذا الجهاز. بلا حساب، وبلا رفع أي شيء.",
    },
    "wizard.welcome.button": {"en": "Set up", "ar": "لنبدأ"},
    "wizard.language.title": {
        "en": "What will you speak?",
        "ar": "بأي لغة ستتكلم؟",
    },
    "wizard.language.lede": {
        "en": "Telling it beats letting it guess. Detection runs per phrase, "
              "and a phrase of near-silence is what it most often gets wrong "
              "— confidently, and in the wrong language.",
        "ar": "إخباره أفضل من تركه يخمّن. الاكتشاف يجري لكل عبارة، وأكثر ما "
              "يخطئ فيه عبارة تكاد تكون صامتة — بثقة، وبلغة خاطئة.",
    },
    "wizard.model.title": {
        "en": "Choose how good it should be",
        "ar": "اختر مستوى الدقة",
    },
    "wizard.model.lede_gpu": {
        "en": "Bigger is more accurate and slower to download. A discrete GPU "
              "was found, so the best one is worth it.",
        "ar": "الأكبر أدقّ وأبطأ في التنزيل. تم العثور على بطاقة رسوميات "
              "منفصلة، لذا يستحق الأفضل العناء.",
    },
    "wizard.model.lede_nogpu": {
        "en": "Bigger is more accurate and slower to download. No discrete GPU "
              "here, so the smaller ones are the practical choice.",
        "ar": "الأكبر أدقّ وأبطأ في التنزيل. لا توجد بطاقة رسوميات منفصلة هنا، "
              "لذا الأصغر هو الخيار العملي.",
    },
    "wizard.model.recommended": {
        "en": "recommended for this machine",
        "ar": "الأنسب لهذا الجهاز",
    },
    # A requirement, not a prediction. What this model does on a particular
    # machine is that machine's business; what it *needs* is the same
    # everywhere, and is the honest thing to put in front of someone about to
    # download 1.5 GiB.
    "wizard.model.needs_gpu": {
        "en": "Needs a discrete graphics card to keep up with speech.",
        "ar": "يحتاج بطاقة رسوميات منفصلة ليواكب الكلام.",
    },
    "wizard.model.size": {"en": "{megabytes} MB", "ar": "{megabytes} م.ب"},
    "wizard.model.here": {
        "en": "already on this machine",
        "ar": "موجود على هذا الجهاز",
    },
    # Said out loud rather than quietly skipped. A file this program does not
    # publish cannot be checked against anything, and the difference between
    # "verified" and "assumed" is the whole reason the other cards can be
    # trusted.
    "wizard.model.unverifiable": {
        "en": "Not one of the three, so there is no published copy to check it "
              "against.",
        "ar": "ليس أحد النماذج الثلاثة، فلا توجد نسخة منشورة للتحقّق منه "
              "بمقارنتها.",
    },
    "wizard.model.use": {"en": "Use this one", "ar": "استخدم هذا"},
    "wizard.model.choose": {"en": "Choose a file…", "ar": "اختر ملفًا…"},
    "wizard.model.not_a_model": {
        "en": "{path} is not a whisper model file.",
        "ar": "{path} ليس ملف نموذج whisper.",
    },
    "wizard.model.not_local": {
        "en": "That file is not on this machine. Copy it here first.",
        "ar": "هذا الملف ليس على هذا الجهاز. انسخه إلى هنا أولًا.",
    },
    "wizard.checking": {
        "en": "Checking it against the published copy",
        "ar": "التحقّق منه بمقارنته بالنسخة المنشورة",
    },
    "wizard.download": {"en": "Download", "ar": "تنزيل"},
    "wizard.downloading": {"en": "Downloading", "ar": "جارٍ التنزيل"},
    "wizard.progress": {
        "en": "{model} · {done} / {total} MB",
        "ar": "{model} · {done} / {total} م.ب",
    },
    "wizard.verified": {"en": "verified", "ar": "تم التحقق"},
    "wizard.failed": {"en": "failed", "ar": "فشل"},
    "wizard.try_again": {"en": "Try again", "ar": "أعد المحاولة"},
    "wizard.next": {"en": "Next", "ar": "التالي"},
    "wizard.mic.title": {
        "en": "Can it hear you?",
        "ar": "هل يسمعك؟",
    },
    "wizard.mic.lede": {
        "en": "Press Test and say something for four seconds. This measures "
              "the same level the silence guard uses, so if it passes here, "
              "takes will not be thrown away as silent.",
        "ar": "اضغط «اختبار» وتكلّم أربع ثوانٍ. يقيس هذا المستوى نفسه الذي "
              "يستخدمه حارس الصمت، فإن نجح هنا فلن تُهمل تسجيلاتك بوصفها صامتة.",
    },
    "wizard.mic.test": {"en": "Test", "ar": "اختبار"},
    "wizard.mic.skip": {"en": "Skip", "ar": "تخطٍّ"},
    "wizard.mic.listening": {"en": "listening…", "ar": "ينصت…"},
    "wizard.mic.heard": {
        "en": "Heard you clearly ({level} dBFS).",
        "ar": "سمعك بوضوح ({level} ديسيبل).",
    },
    "wizard.mic.barely": {
        "en": "Barely anything ({level} dBFS). Check that the right input is "
              "selected and unmuted, then test again.",
        "ar": "لا شيء تقريباً ({level} ديسيبل). تأكّد من اختيار الإدخال الصحيح "
              "وأنه غير مكتوم، ثم أعد الاختبار.",
    },
    "wizard.shortcut.title": {
        "en": "One last thing: the key",
        "ar": "شيء أخير: المفتاح",
    },
    "wizard.shortcut.lede": {
        "en": "Wayland gives no way for an application to claim a shortcut "
              "for itself, so this part is yours.",
        "ar": "لا يتيح Wayland لأي تطبيق أن يحجز اختصاراً لنفسه، لذا هذا "
              "الجزء متروك لك.",
    },
    "wizard.done": {"en": "Done", "ar": "تم"},
    "wizard.shortcut.bind": {"en": "Add it for me", "ar": "أضِفه عني"},
    "wizard.shortcut.bound": {
        "en": "Added to {path}. A copy of the old file is beside it.",
        "ar": "أُضيف إلى {path}. نسخة من الملف القديم بجانبه.",
    },
    "wizard.shortcut.reload": {
        "en": "Reload your compositor's configuration for it to take effect.",
        "ar": "أعِد تحميل إعدادات مدير النوافذ ليصبح فعّالاً.",
    },
    "wizard.shortcut.already": {
        "en": "{path} already binds this key.",
        "ar": "{path} يربط هذا المفتاح أصلاً.",
    },
    "wizard.shortcut.failed": {
        "en": "Could not write {path}: {error}",
        "ar": "تعذّرت الكتابة إلى {path}: {error}",
    },

    # -- how to bind the key, per compositor -------------------------------
    #
    # Only the sentence is translated. The lines under it are configuration
    # to be pasted verbatim, and translating a config key would be a bug that
    # looks like a translation.
    # The path is a field rather than part of the sentence so that it can be
    # isolated. Written into the Arabic directly it came out as
    # ":config/hypr/hyprland.conf./~ أضف إلى" -- the slashes, the dot and the
    # colon are all direction-neutral, so the bidirectional algorithm laid
    # them out against the Arabic and produced a path that is wrong to read
    # and wrong to type.
    "shortcut.hyprland": {
        "en": "Add to {path}:",
        "ar": "أضِف إلى {path}:",
    },
    "shortcut.sway": {
        "en": "Add to {path}:",
        "ar": "أضِف إلى {path}:",
    },
    "shortcut.niri": {
        "en": "Add to {path}, inside binds {{}}:",
        "ar": "أضِف إلى {path}، داخل binds {{}}:",
    },
    "shortcut.kde": {
        "en": "System Settings → Keyboard → Shortcuts → Add → Command",
        "ar": "إعدادات النظام ← لوحة المفاتيح ← الاختصارات ← إضافة ← أمر",
    },
    "shortcut.gnome": {
        "en": "Settings → Keyboard → View and Customise Shortcuts → Custom",
        "ar": "الإعدادات ← لوحة المفاتيح ← عرض الاختصارات وتخصيصها ← مخصّص",
    },
    "shortcut.generic": {
        "en": "Bind a key to this command, however your desktop does that:",
        "ar": "اربط مفتاحاً بهذا الأمر، بالطريقة التي يتيحها سطح مكتبك:",
    },

    # -- the settings window ------------------------------------------------
    "settings.title": {"en": "Dictation", "ar": "الإملاء"},
    "settings.tab.engine": {"en": "Engine", "ar": "المحرّك"},
    "settings.tab.microphone": {"en": "Microphone", "ar": "الميكروفون"},
    "settings.tab.history": {"en": "History", "ar": "السجل"},
    "settings.language": {"en": "Language", "ar": "اللغة"},
    "settings.ui_language": {"en": "App language", "ar": "لغة التطبيق"},
    "settings.ui_language.auto": {"en": "Follow the system", "ar": "تبعاً للنظام"},
    "settings.ui_language.hint": {
        "en": "Close and reopen this window to see the change. Separate from "
              "the language above, which is the one you speak.",
        "ar": "أغلق هذه النافذة وافتحها لترى التغيير. منفصلة عن اللغة أعلاه، "
              "وهي اللغة التي تتكلمها.",
    },
    "settings.model.remove": {"en": "Delete", "ar": "احذف"},
    "settings.model.remove.confirm": {
        "en": "Delete {model} from this machine?",
        "ar": "حذف {model} من هذا الجهاز؟",
    },
    "settings.model.removed": {
        "en": "Deleted. {megabytes} MB is back.",
        "ar": "حُذف. عاد {megabytes} م.ب.",
    },
    "settings.model.remove.last": {
        "en": "This is the only model installed. Setup will open and offer to "
              "fetch another.",
        "ar": "هذا النموذج الوحيد المثبَّت. سيُفتح الإعداد ويعرض جلب غيره.",
    },
    "settings.history.clear": {"en": "Delete all", "ar": "احذف الكل"},
    "settings.history.clear.confirm": {
        "en": "Delete {count} transcripts and their audio?",
        "ar": "حذف {count} تفريغًا وملفاتها الصوتية؟",
    },
    "settings.history.cleared": {
        "en": "Deleted. Nothing of what you said is left on this machine.",
        "ar": "حُذفت. لم يبقَ على هذا الجهاز شيء مما قلته.",
    },
    "settings.record.start": {"en": "Start speaking", "ar": "ابدأ الكلام"},
    "settings.record.stop": {"en": "Stop and type it", "ar": "أوقِف واكتبه"},
    "settings.record.working": {"en": "Typing it…", "ar": "يكتبه…"},
    "settings.record.hint": {
        "en": "For when there is no key to press. The shortcut is faster once "
              "you have one.",
        "ar": "لحين لا يكون هناك مفتاح تضغطه. الاختصار أسرع بمجرد أن يصبح لديك "
              "واحد.",
    },
    "settings.model": {"en": "Model", "ar": "النموذج"},
    "settings.model.hint": {
        "en": "Switching unloads the running server; the next dictation loads "
              "the new model while you speak.",
        "ar": "التبديل يفرغ الخادم العامل؛ والإملاء التالي يحمّل النموذج "
              "الجديد بينما تتكلم.",
    },
    "settings.vocabulary": {"en": "Vocabulary", "ar": "المفردات"},
    "settings.vocabulary.hint": {
        "en": "Fed to whisper as its initial prompt. Mixing Arabic and Latin "
              "here is what keeps spoken tech terms spelled rather than "
              "transliterated. Keep it short — a long prompt leaks into the "
              "transcript.",
        "ar": "تُمرَّر إلى المحرّك تلميحاً أولياً. خلط العربية باللاتينية هنا "
              "هو ما يبقي المصطلحات التقنية المنطوقة مكتوبة بحروفها بدل "
              "تحويلها. أبقِها قصيرة — التلميح الطويل يتسرّب إلى النص.",
    },
    "settings.input": {"en": "Input", "ar": "الإدخال"},
    "settings.input.muted": {"en": "{name} (muted)", "ar": "{name} (مكتوم)"},
    "settings.not_measured": {"en": "Not measured yet.", "ar": "لم يُقَس بعد."},
    "settings.switching": {"en": "Switching input…", "ar": "يبدّل الإدخال…"},
    "settings.switched": {
        "en": "Input switched. Test it to see the level.",
        "ar": "تم تبديل الإدخال. اختبره لترى المستوى.",
    },
    "settings.switch_failed": {
        "en": "Could not switch input: {error}",
        "ar": "تعذّر تبديل الإدخال: {error}",
    },
    "settings.test": {"en": "Test microphone (4s)", "ar": "اختبر الميكروفون (٤ ثوانٍ)"},
    "settings.recording": {"en": "Recording…", "ar": "يسجّل…"},
    "settings.level": {"en": "{name}: {level} dBFS", "ar": "{name}: {level} ديسيبل"},
    "settings.above_gate": {
        "en": "  — above the gate, speech will be transcribed.",
        "ar": "  — فوق العتبة، سيُفرَّغ الكلام.",
    },
    "settings.below_gate": {
        "en": "  — at or below the {threshold} dBFS gate, this would be "
              "discarded as silence.",
        "ar": "  — عند عتبة {threshold} ديسيبل أو دونها، سيُهمل بوصفه صمتاً.",
    },
    "settings.test_failed": {"en": "Test failed: {error}", "ar": "فشل الاختبار: {error}"},
    "settings.keep_audio": {
        "en": "Keep the recording of every dictation",
        "ar": "احتفظ بتسجيل كل إملاء",
    },
    "settings.keep_audio.hint": {
        "en": "Kept takes appear with a play button in History, which is the "
              "only way to tell a misheard word from a badly spoken one. They "
              "are never deleted automatically — a day of dictation is a lot "
              "of audio.",
        "ar": "تظهر التسجيلات المحفوظة مع زر تشغيل في السجل، وهي الطريقة "
              "الوحيدة للتمييز بين كلمة أُسيء سماعها وأخرى أُسيء نطقها. لا "
              "تُحذف تلقائياً أبداً — يوم من الإملاء صوت كثير.",
    },
    "settings.gate.hint": {
        "en": "Speak while it records. Anything at or below {threshold} dBFS "
              "is discarded as silence rather than transcribed, so a reading "
              "below that while you are speaking means the level is too low — "
              "not that dictation is broken.",
        "ar": "تكلّم أثناء التسجيل. كل ما كان عند {threshold} ديسيبل أو دونه "
              "يُهمل بوصفه صمتاً بدل تفريغه، فقراءة أدنى من ذلك وأنت تتكلم "
              "تعني أن المستوى منخفض — لا أن الإملاء معطّل.",
    },
    "settings.no_transcripts": {"en": "No transcripts yet.", "ar": "لا نصوص بعد."},
    "settings.seconds": {"en": "{seconds}s", "ar": "{seconds} ث"},
}
