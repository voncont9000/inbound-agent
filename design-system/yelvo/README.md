Yelvo is the system for a smart voice agent. Ink and cool greys carry the interface. Yelvo yellow is the one brand colour, used in bold solid blocks and never as text on light grounds. Headlines are set in a heavy, friendly grotesque; everything else stays quiet.

## Logo

- Use `yelvo-wordmark.svg` on `surface`, `surface-secondary` and `accent` grounds; use `yelvo-wordmark-on-dark.svg` on ink or dark photography.
- Below 80px wide, use `yelvo-mark.svg` (the speaking dot) alone.
- App icons are `yelvo-app-icon.svg` (default) and `yelvo-app-icon-dark.svg`. Prefer the yellow icon; the ink one disappears on dark docks.
- Keep clear space around the wordmark equal to the dot's diameter. Never recolour, outline, stretch, add effects, or set the name in live type as a substitute.
- The yellow bars inside the ink dot must stay yellow. On a yellow ground the dot stays ink.

## Content fundamentals

- Write in plain, confident English. Short declarative headlines with no exclamation marks: "Calls answered, every time", "Integrates with your tools".
- Address the reader as "you"; the product speaks as "we" only in support copy.
- Sentence case everywhere, including buttons and nav. Never Title Case; caps only in `micro` badges.
- Lead with time and effort saved, with concrete numbers: "ready to test in 30 seconds", "around the clock".
- No emoji in product or marketing copy.
- Reassurance lines are fragments with a check icon: "Free personalised agent", "No signup".

## Colour

- Build every screen on `surface` and `surface-secondary` with `ink` headings and `body` paragraphs. Alternate section bands between the two surfaces.
- `accent` (Yelvo yellow) is a fill colour. Use it for one bold block per viewport: a hero panel, a highlighted pricing card, the accent button, selected chips. Everything on it is `on-accent` ink.
- Yellow fails as text, icons, borders and rings on light grounds (1.4:1). For accent coloured words, numerals, icons and the focus ring use `accent-text`, a deep ochre that passes 6.1:1 on `surface`. In dark mode `accent-text` becomes yellow itself.
- `accent-soft` (Butter) is for soft panels and avatar discs, with `on-accent-soft` text. `accent-tint` is the faintest wash, behind badges.
- The primary call to action is `cta` (near black). A yellow `accent` button is for a single secondary highlight, never two side by side.
- No gradients. Yelvo's colour is always flat.
- `muted` in light mode is decorative only (2.4:1). Placeholders that must be read use `body`.

## Type

- Headlines (`display`, `h1`, `h2`, `h3`) are Bricolage Grotesque, the logo's typeface: 800 for `display` and `h1`, 700 below, with negative tracking. Below 640px `h1` drops to 30px and `h2` to 28px.
- Everything else is Roboto at 400, and 500 for titles and labels. Load both from Google Fonts.
- Paragraphs under headings are `lead` in `body`, max width about 640px.
- Annotation Mono at 500 (`note`) is the annotation voice, used only for step numerals and the odd margin note.

## Spacing and layout

- 4px base grid. Card padding `space-6`, grid gaps `space-8`, heading to content `space-12`, section padding `space-24` on desktop and `space-16` on mobile, page gutter `space-4` on mobile.
- Content is centred in a 1200px max container. Hero and section headings are centred; feature rows alternate text and media.

## Shape, borders and depth

- Radii step up with size: `radius-sm` on pills and badges, `radius-button` on small buttons and inputs, `radius-md` on large buttons, `radius-lg` on cards, `radius-xl` on hero media and yellow blocks.
- Separate with `border` hairlines first, shadow second. Cards rest on `shadow-card`; only popovers and hovered cards get `shadow-pop`.
- Controls (inputs, outline buttons) use `border-control` so their edge reaches 3:1.

## States and motion

- Focus: a solid 3px `ring` outline offset by 2px on every interactive element. It is ochre in light mode and yellow in dark.
- Hover darkens `cta` to `cta-hover`, lightens `accent` to `accent-hover`, or swaps `surface` for `surface-secondary`. Buttons nudge down 1px when pressed.
- Default transitions are 150ms `cubic-bezier(.4,0,.2,1)`. Reveals and accordions use `cubic-bezier(.19,1,.22,1)` at 300ms.

## Iconography

- Thin 1.6px stroke line icons on a 16px grid, rounded caps and joins, coloured with `currentColor`. Lucide matches this style if a full set is needed.
- Icons are `ink` by default and `accent-text` when they need emphasis. Never yellow icons on white.
- The three rounded sound bars from the logo may be reused as a "listening" or "speaking" indicator. No other logo fragments become icons.
- No emoji, no filled illustration icons, no mascots.

## Imagery

- Real product screenshots in `radius-xl` frames, set on a flat `accent` block or `surface-secondary`.
- Customer photos are real people, cropped tight, in `radius-full` circles.
