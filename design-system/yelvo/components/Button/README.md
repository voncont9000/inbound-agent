# Button

The one action control. Provide `children` (the label), and optionally `variant`, `size`, `icon` and `href`.

- `cta` (ink fill, `cta` token) is the primary action. One per view, usually "Sign up now" or "Create your own AI".
- `accent` (Tangerine fill, `on-accent` label) is for a single secondary highlight such as a video tour. Never put two accent buttons side by side, and never set white text on it.
- `outline` is the default for secondary actions like "Book a demo". Its edge uses `border-control`.
- `ghost` is for tertiary nav style actions.
- `size: "sm"` is 38px high with `radius-button`, used in the nav and inline. `size: "lg"` is 48px high with `radius-md`, used in heroes and section ends.
- `icon: "arrow"` appends a trailing arrow on forward moving actions only.
- Labels are sentence case, verb first, three words or fewer where possible. Weight stays 400; only `accent` goes to 500.
- Passing `href` renders an anchor with identical styling.
