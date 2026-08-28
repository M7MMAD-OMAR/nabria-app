# The landing page — analysis and plan

Planning only. Nothing here is built yet.

## The brief

| | |
|---|---|
| Style | **everything is drawn.** Shapes, strokes, no photographs, no screenshots |
| Scale | large — a section should read from across the room |
| Text | almost none. Labels and numbers, not sentences |
| Job | say what it is, show it working, hand over a download |
| Never | name another application, on any page |

## Why it is all drawn, and what that costs

A screenshot of this app is a 76×30 pixel pill. There is nothing to show. The
product has no window, no dashboard and no interface — that is its whole
argument — so a conventional product page would be a picture of somebody
else's text editor.

Drawing it is therefore not decoration but the only honest option: the page can
show the pill at 8× and animate the five marks the way they actually move.

Two real costs, both fixable, neither optional:

- **A page of pictures says nothing to a screen reader or a search engine.**
  Every figure needs a `<title>`, and each section needs one real sentence in
  the markup even when the design shows none. Do not skip this — it is the
  difference between a page that is minimal and one that is empty.
- **No screenshot means no proof.** The speed section carries the numbers from
  `docs/DESIGN.md` and nothing beyond them.

## The visual language is already in the repo

`orb.py` is 344 lines of Cairo drawing and `theme.py` is the palette. The page
uses those values rather than inventing its own, so the site looks like the
application instead of like a page about it.

| | value | from |
|---|---|---|
| pill | 76 × 30, corner radius 13, inset 3 | `orb.WINDOW_W`, `PILL_RADIUS`, `PILL_INSET` |
| marks | 5, width 3, from 3 to 16 tall, gap 8 | `orb.BAR_*` |
| shape of a voice | 0.5, 0.82, **1.0**, 0.82, 0.5 — tallest in the middle | `orb.BAR_ENVELOPE` |
| the voice | `#ff9d7d` | `theme.DARK["primary"]` |
| thinking | `#f0c48a` | `theme.DARK["tertiary"]` |
| wrong | `#ff6f5e` | `theme.DARK["error"]` |
| the pill | `#1c1613`, edge `#4a3a35` | `surface_container`, `outline_variant` |
| page ink | `#f4e6e0` | `on_surface` |
| a raised panel | `#241d19` | `card` |

**Page scale is 8×**, so a 3 px mark becomes a 24 px stroke and the pill is
608 px wide. One stroke unit follows from that: `8px` for anything structural,
`4px` for anything secondary, and nothing thinner — a hairline breaks the
"drawn at size" reading and disappears on a phone.

**Dark only.** The palette is a dark theme; a light variant would be a second
design to keep true. The page and the product should be the same object.

## Sections

Ordered as they would appear. `core` is the page I would ship; the rest is a
menu.

| | section | drawn as | words | |
|---|---|---|---|---|
| 1 | **Hero** | the pill at 8×, marks moving as they do while recording | ~6 | core |
| 2 | **Three beats** | key → voice → text arriving in a window outline | 3 labels | core |
| 3 | **It stays here** | a drawn machine; every arrow turns back inside it | ~4 | core |
| 4 | **Speed** | three bars, lengths to scale: 0.3 · 0.9 · 21 | numbers | core |
| 5 | **Arabic** | a spoken line and the same line landing as text | ~4 | core |
| 6 | **Download** | the command, in a drawn frame | command | core |
| 7 | **Honest limits** | four small drawn marks | ~12 | core |
| 8 | **The five states** | the pill five times: rest, voice, thinking, done, failed | 5 labels | strong |
| 9 | **What it is not** | crossed-out shapes — a tray, a cloud, a sidebar | ~6 | strong |
| 10 | **Nothing is lost** | the transcript reaching the disk before it reaches the window | ~5 | maybe |
| 11 | **Open source** | a drawn licence mark, a link | ~3 | maybe |

### On each

**1 · Hero.** The only animated thing on the page. Five marks, CSS keyframes on
the `height` of five rects, envelope `0.5/0.82/1.0/0.82/0.5`, attack faster
than release the way `LEVEL_ATTACK` and `LEVEL_RELEASE` are. One line under it,
and the install command. Nothing else above the fold.

**2 · Three beats.** Press · speak · it is typed. The third drawing is the only
place the page shows a window at all, and it is an outline, not a screenshot.

**3 · It stays here.** The claim the product is actually built on. Drawn as a
box with the arrows curving back into it — the picture makes the point without
a paragraph about privacy, which is the one topic where a paragraph reads as a
sales pitch.

**4 · Speed.** Bars to true scale. 0.3 s and 21 s at the same scale is a bar
you can barely see against one that crosses the screen, which is the entire
argument for choosing the model by hardware. Numbers only — the moment this
section gains a sentence it becomes a benchmark claim rather than a picture.

**5 · Arabic.** The differentiator, and it must be shown rather than asserted.
Draw a wave, then the Arabic text it produced, right-aligned, in a real font.
This is the one place on the page where type is the illustration.

**6 · Download.** See below.

**7 · Honest limits.** Wayland only · bind your own key · Linux · no sandboxed
build. Four marks, no excuses attached. A page that hides these attracts the
people who bounce, and it would contradict the README, which says all four.

**8 · The five states.** The strongest addition on the list. It is a drawing
the app already contains, it explains the only interface there is, and it is
the section that most obviously could not be a screenshot.

**9 · What it is not.** The non-goals are load-bearing (`PLAN.md`), and drawn
as crossed-out shapes they cost six words. It also does the competitive framing
without naming anyone — which is the constraint.

**10 · Nothing is lost.** True, and it took work, but it answers a question
nobody has yet on a first visit. Cut it if the page feels long.

## Download — what the link actually is

Settled before the page, because the page cannot be written around a link that
does not exist — and it did not. The `v0.1.0` tag had no release attached, and
was eleven commits behind besides: no prebuilt engine, no portal shortcuts, and
the `pycairo` bug still in it. Publishing it would have handed the first
visitor a version that makes them compile.

| offer | link | state |
|---|---|---|
| **one line** | `curl -fsSL …/releases/latest/download/install-nabria.sh \| sh` | works; runs the installer verified in three distro containers |
| **tarball** | `…/releases/latest/download/nabria.tar.gz` | works; for anyone who will not pipe a script into a shell |
| **source** | the repository | works |
| AppImage | — | **do not put on the page.** Nothing has been built; see `PLAN.md` |
| distro packages | — | later, and community-owned. Do not draw a Fedora or Arch mark next to a link that does not exist |

Under the command, in small text: Linux · Wayland · glibc 2.34+. That is not a
limitation section, it is what stops a Windows visitor from running a command
that cannot work.

## Build and hosting

| | |
|---|---|
| where | GitHub Pages, `main` branch, `/docs` folder |
| why there | no workflow, no build, nothing that can fail — the objection to CI applies here too |
| files | `docs/index.html`, `docs/.nojekyll` |
| domain | `nabria.sbarah.com` via CNAME, later |
| assets | inline SVG, inline CSS. No framework, no image files |
| script | none. The hero animates with CSS keyframes |
| budget | one file under 60 KB, one request |

`.nojekyll` is not optional: Pages runs Jekyll on a branch source by default,
which would try to render `docs/DESIGN.md` and `docs/SITE.md` as pages.

## Open questions

1. **A font, or the system stack?** The Arabic section is type-as-illustration
   and the system stack renders it differently on every machine. One
   self-hosted variable font, subset, is maybe 20 KB. Leaning yes, for that one
   section.
2. **An Arabic version of the page?** The product is Arabic-first and the page
   has almost no words, so `/ar` with `dir="rtl"` is nearly free. Leaning yes.
3. **A recording of it working?** The most convincing thing possible, and the
   only thing on this page that would not be drawn. A silent, short screen
   capture. Against the brief as written — ask before adding.
4. **Motion.** Does anything animate besides the hero? Respect
   `prefers-reduced-motion` either way.

## Not on the page

Named applications, of any kind. Model names and version numbers. Anything the
repository cannot prove. A newsletter, a star count, a testimonial, a roadmap.
