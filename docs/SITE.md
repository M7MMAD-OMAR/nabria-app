# The landing page — analysis and plan

Implemented in `docs/index.html` and `docs/ar/index.html`. This document remains
the source of truth for the landing page's visual and content decisions.

## The brief

| | |
|---|---|
| Style | product story drawn from the orb geometry; setup shown with real screenshots |
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

**Dark is the anchor, not the only ground.** The hero, privacy section and
footer use the app's dark palette. Warm cream, coral and gold sections separate
the product story without introducing colours outside the shipped palette.

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

**4 · What it needs.** Not speed. A bar chart of seconds would be a benchmark
of one laptop presented as a property of the software, and the honest version
of that picture is different on every machine that loads the page. Draw the
requirement instead: three cards — smaller, middle, largest, unnamed — each
with its download size and the memory it wants, and a mark on the one that
asks for a graphics card. Sizes are facts about the models; times are facts
about hardware, and this page has no way to know the reader's.

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
| where | a Cloudflare Worker, `nabria-site`, serving `/docs` as static assets |
| why there | no workflow, no build, nothing that can fail — the objection to CI applies here too |
| pages | `docs/index.html`, `docs/ar/index.html` |
| domain | `nabria.sbarah.com`, a Workers custom domain on the `sbarah.com` zone |
| assets | shared CSS and JS, generated Nabria SVG mark, social image, real setup screenshots |
| icons | local Phosphor Duotone sprite with the upstream MIT licence |
| script | animated mobile drawer, screenshot carousel, quick install, install tabs and command copy |
| budget | static files, no framework and no build step |

Not GitHub Pages, which was the earlier plan: `sbarah.com` is already on
Cloudflare and every other subdomain of it is a Worker, so a Pages site would
have been the one host in the account nobody else uses. The trade is the same
either way: a directory of files goes up as it stands. `docs/.nojekyll` stays
for anyone who points Pages at this folder anyway.

`docs/.assetsignore` keeps `*.md` out of the deployed site. `SITE.md` and
`DESIGN.md` are notes for the repository, not pages, and an assets directory
publishes everything in it at a bare URL.

The deploy is `wrangler deploy` from the repository root, and it ships the
working tree, so commit first, exactly as `release_tarball` uses `git archive`
rather than `tar` for the same reason. Connecting the Worker to the repository
in the Cloudflare dashboard (Workers Builds, `main`, deploy command
`wrangler deploy`) removes that footgun; it cannot be done from the CLI.

## Settled

Three of the four open questions answered themselves once the application
became bilingual and grew screenshots. Recorded here rather than asked again.

1. **A self-hosted font, for the Arabic section.** One variable build, subset,
   about 20 KB. The system stack renders that section differently on every
   machine, and the section is type-as-illustration — the type *is* the
   picture, so leaving it to the visitor's font list is leaving the
   illustration to chance.
2. **Yes to `/ar`.** The application is now written in Arabic and English
   throughout; a page that is only in English would be describing a product it
   is not itself an example of. `dir="rtl"` and the same drawn components —
   the page has almost no words, which is exactly what makes the second
   language nearly free.
3. **Screenshots, then no screenshots.** The all-drawn brief and real pictures
   of the application were in tension, and the page first resolved that toward
   the pictures: a setup section carrying a carousel of six per language. That
   section is now cut, on the owner's call, as a stretch of page that earned
   its length only by having images to show. `docs/screenshots/` stays in the
   repository because the README uses it, and `docs/.assetsignore` keeps it out
   of the deployed site. So the brief is back where it started: everything on
   the page is drawn. A recording stays out either way, being the one thing
   that could not be checked into the repository and re-generated.

## Motion, and the one thing it has to say

**Settled: the hero plays a whole take, slowly.** The microphone waits, hears a
voice, the indicator arrives with the second keypress, and only then is the
sentence typed. That order is the product: the words land *after* you stop
talking, and a page that types while the waveform is still moving would be
claiming live transcription, which is an explicit non-goal in `PLAN.md`. The
whole cycle runs about fourteen seconds, which is slow for a web animation and
right for a thing whose argument is that you can stop paying attention to it.

Three constraints on it, all of them the reason the code looks the way it does:

- **The sentence is in the markup**, and JavaScript reads it back out before
  animating. A page whose only copy arrives from a script is a page with no
  copy, which is the first cost named at the top of this document.
- **It is announced once, not once per character.** The paragraph takes
  `role="img"` and an `aria-label`; the node being typed into is hidden from
  assistive technology.
- **`prefers-reduced-motion` gets the finished sentence and nothing else**, and
  the loop stops entirely when the hero is scrolled out of view. The global
  reduced-motion rule in the stylesheet cannot do this on its own, because it
  only shortens CSS durations and the typing is driven from JavaScript.

**The hero panel is dark.** It was cream, which put the one white rectangle on
the page in the middle of the darkest section and read as a screenshot of some
other program's window. It now uses the shipped surfaces, `#211916` on
`#1c1613`, so the section is one material rather than two.

## Support

The section is `05`, on the dark ground, between the requirements strip and the
closing line. Its argument is the one the README already makes: the thing is
MIT and runs on your own machine, so there is no account to sell and no usage
to meter, and what it costs is time. The panel beside it says that as three
marks rather than a second paragraph.

Buy Me a Coffee's button and cup ship as their own files, unmodified, and their
terms are noted in `docs/assets/BMC-BRAND.txt`. Two rules follow from that and
neither is cosmetic:

- **Their yellow is not in the palette.** `#FFDD00` and `#0D0C22` appear only
  inside the support section's rules. The block at the top of `site.css` is the
  application's colours, from `theme.py`, and that claim stops being true the
  moment a second brand's values are filed next to them.
- **The button is not redrawn.** It is their mark, so it is their file. It also
  never mirrors: the Arabic page tilts the panel the other way, as the hero
  window does, and leaves the button alone.

The README carries the same button from Buy Me a Coffee's own CDN rather than a
copy in this repository, because a raster committed at the root of `docs/`
would ride into `nabria.tar.gz` through `git archive` for no reason.

## Cut

**The setup section.** Four screenshots of the wizard, a heading and three
lines, sitting between the Arabic section and the download. It was cut whole on
the owner's call. The argument for cutting is the brief's own: the page's job
is to say what this is, show it working and hand over a download, and a tour of
a first-run wizard is none of the three. The screenshots survive in the README,
where somebody deciding whether to install has a reason to want them.

**Tashkeel, in the Arabic page.** The brand was set as `نَبْرة` throughout and
the hero read `احكِ`. Both are gone: the page is written unvocalised, and the
hero verb is `تكلم`. This is a house rule for the site's copy, not a claim
about the word.

## Not on the page

Named applications, of any kind — this is a standing rule for everything
outward-facing, not a preference for this page. **Timings, benchmarks, or any
number measured on one machine and presented as a property of the software.**
Model names and version numbers. Anything the repository cannot prove. A newsletter, a star count, a
testimonial, a roadmap.
