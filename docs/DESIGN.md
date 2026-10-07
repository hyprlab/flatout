# Design system

Flatout has two looks. The **admin** uses the Hyprlab design described on
this page: Hyprfeed's "electric editorial", Inter everywhere, true neutral
greys,
and one accent color spent sparingly, in `static/css/app.css`. The **public
site** belongs to whoever runs it: `static/site/site.css` draws it from
tokens the owner sets in Design (see [The public site](#the-public-site)).

## Principles

- **The accent is scarce.** It marks new items and the active
  state (the current nav row, chip, focused field). Used anywhere else it
  stops meaning anything.
- **Surfaces carry the hierarchy**, not borders and shadows: `--bg` for the
  page, `--panel` for the sidebar and quiet containers, `--surface` for cards
  and dialogs, `--field` for inputs. Every surface, line and text color is a
  true grey (equal red, green and blue), so the accent is the only color.
- **Motion explains where something went.** Dialogs rise into place and a
  toast confirms what happened. Every animation is off under
  `prefers-reduced-motion`.
- **Nothing shifts.** List rows keep their grid cells when a marker hides; the
  settings window keeps its size between sections; the scrollbar gutter is
  reserved so opening a dialog doesn't move the page.
- **Self-contained.** Inter is embedded, icons are inline SVG, and nothing is
  loaded from a CDN. Turnstile, when enabled, is the only third-party request.

## Tokens

Set on `:root` and per theme on `html[data-theme="light"|"dark"]`.

| Token | Use |
| --- | --- |
| `--accent`, `--accent-ink`, `--accent-glow` | The brand color, text on it, and its glow. The only brand values in the file. |
| `--bg`, `--panel`, `--surface`, `--field` | The four surface levels |
| `--ink`, `--muted`, `--faint` | Text: primary, secondary, tertiary. Each reads at 4.5:1 or better (WCAG AA) on every surface in both themes; a new shade must too |
| `--line`, `--line-strong` | Rules and borders |
| `--wash` | The accent at low opacity: washes behind chosen chips and cards |
| `--selected`, `--hover` | The chosen row (sidebar, settings, the editor's lists) and a row under the pointer: a neutral grey a step darker than the panel in the light theme and a step lighter in the dark |
| `--scrim` | Behind dialogs and the mobile sidebar |
| `--danger` | Destructive actions and errors; lighter in the dark theme, so it reads at 4.5:1 there as well |
| `--radius`, `--radius-sm` | 14px for cards and panels, 10px for controls |
| `--shadow-1`, `--shadow-2` | Resting and raised |
| `--sidebar-w`, `--topbar-h` | Shell dimensions |

To rebrand, change the three accent tokens in both themes (the dark theme uses
a slightly different accent for contrast) and replace the icon: `static/img/icon.png`
(256px), `apple-touch-icon.png` (180px) and `favicon.ico` (16, 32 and 48px), all
cut from one square picture.

## Type

Inter Variable, 15px body at 1.55. Headings use heavy weights (750 to 850)
with negative tracking (-0.02em to -0.035em). Labels are 11px, 650 to 700
weight, uppercase, tracked +0.07em to +0.09em, in `--muted` or `--faint`.
Long-form text uses `.prose` at 16.5px and 1.72.

## Components

| Class | What it is |
| --- | --- |
| `.btn` + `--primary`, `--ghost`, `--danger`, `--block`, `--xs` | Buttons. One primary per view. |
| `.iconbtn` + `--sm`, `--danger`, `.is-busy` | Square icon buttons; `.is-busy` spins the icon |
| `.field`, `.field-label`, `.check`, `.hint`, `.form-error` | Form parts |
| `.seg` | Segmented control over radio inputs |
| `.theme-picker`, `.theme-chip` | Chip-style radio group |
| `.chip`, `.chip--muted`, `.count`, `.count--accent`, `.title-chip` | Small labels and counters |
| `.shell`, `.sidebar`, `.sidebar-head/-scroll/-foot` | The layout. The head and foot stay pinned; only the middle scrolls. |
| `.navitem`, `.sidebar-label`, `.sidelist`, `.sideitem` | Sidebar rows. `.is-active` adds `--selected` and an inset accent bar. |
| `.topbar`, `.context-title`, `.topbar-actions` | The sticky, blurred bar over the content |
| `.modal`, `.modal--wide`, `.modal-head`, `.modal-body` | Dialogs, built on `<dialog>` |
| `.settings`, `.settings-nav`, `.settings-navitem`, `.settings-head`, `.settings-pane` | The settings window: a rail of sections beside the chosen one; on phones a list that slides into each section |
| `.toast`, `.toast--error`, `.toast-action` | Confirmations under the topbar, with an optional action such as Undo |
| `.about-hero`, `.tech-stack`, `.release-list` | The About section |
| `.auth-card`, `.auth-mark`, `.flash`, `.wizard`, `.wiz-*`, `.error-code` | Sign-in, setup and error pages |

## Interface rules

- **Everyone can use it.** Every control has a name a screen reader can read
  (visible text, or `aria-label` on an icon button), and every field a label;
  a placeholder is not one. Anything tapped on a phone gets at least 24 px.
- **Errors are shown; success mostly isn't.** A failed action always says what
  went wrong, in a sentence, as an inline form error or an error toast. A
  success toast is for actions whose result is not already visible (a saved
  preference, a deleted record with Undo), not for every click.
- **Undo instead of "Are you sure?"** for anything recoverable. A confirmation
  dialog is kept for what can't be undone: deleting an account.
- **Optimistic, then honest.** A toggle updates at once and rolls back with an
  error toast if the server refuses.
- **Every screen has a URL.** The editor keeps the open part in the hash
  (`/admin/site#section/features`), so a reload or a link comes back to it.
- **A reload keeps your place.** Refreshing brings back the dialog that was
  open, as it was: the settings section and its scroll. Every `<dialog>` gets this by default (one with nothing to
  remember simply reopens); `data-restore="off"` opts one out, and a dialog
  with state adds a save/restore pair to `dialogMemory` in `app.js`. Only a
  reload restores; arriving at the page any other way starts clean.
- **Keyboard first.** Every control is reachable by keyboard, the editor's
  lists have move buttons beside drag and drop, and focus is always visible
  (`:focus-visible` draws the accent outline).
- **Phones get the same app.** Under 900px the sidebar becomes a drawer; under
  700px settings goes full screen as a list of sections, each opening with a
  Back button (Escape goes back too). Under 1000px the editor stacks its form
  above the preview.
- **American spelling** in everything the interface says.

## The admin's own components

| Class | What it is |
| --- | --- |
| `.page-body`, `.panel`, `.panel-title`, `.tiles`, `.tile` | A page's column of panels, and the stat tiles on the Overview, Releases and Installs |
| `.banner--warn` | Something blocking the page's main action, such as a missing signing key |
| `input.switch` | A checkbox drawn as a switch, for showing and hiding |
| `.editor`, `.editor-panel`, `.editor-preview` | The site editor: the form beside the live preview |
| `.ed-row`, `.ed-section`, `.ed-form`, `.ed-field`, `.ed-group`, `.ed-item` | The editor's list of parts and the forms built from the schema |
| `.media-grid`, `.media-card`, `.dropzone` | The media library, the media picker and the bundle upload |
| `.release-row`, `.status--*` | A release in the history, and its state |
| `.chart`, `.chart-tip`, `.data-table` | The installs chart, its tooltip and its table view |

The installs chart is one series, so it has no legend; its color is the
reference categorical blue (`#2a78d6` light, `#3987e5` dark), checked against
both admin surfaces. Columns are at most 24 px wide with a 4 px rounded top, a
2 px gap and hairline grid lines; only the peak and the latest day carry a
label, every column has a tooltip on hover and focus, and the table view
holds every value.

## The public site

`site.css` uses only these tokens, which the page head sets from the theme,
once for light and once for dark:

| Token | Design field |
| --- | --- |
| `--accent`, `--accent-ink` | Links, focus, highlights, and the text on them |
| `--button`, `--button-ink` | The download button |
| `--highlight`, `--highlight-ink` | Checkmarks and the big number |
| `--hero-bg`, `--hero-ink` | The hero and the header over it |
| `--band-bg`, `--band-ink` | The highlight band |
| `--bg`, `--bg-tint`, `--tint-strong`, `--surface` | Page, panels, tinted sections, cards |
| `--ink`, `--ink-soft`, `--line` | Text, secondary text, borders |
| `--code-bg`, `--code-fg` | Command boxes |
| `--c-purple` … `--c-pink`, each with `-ink` | The icon palette, and the glyph color that reads on each |
| `--font-body`, `--font-heading`, `--font-size`, `--heading-weight`, `--radius`, `--maxw` | Type and shape |

Everything else (hover shades, gradients, washes) is derived from these with
`color-mix()`, so a new color scheme never needs a stylesheet change. A new
section type gets its markup in `templates/site/sections/` and its styles in
the additions at the end of `site.css`, using the same tokens.
