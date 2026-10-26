# Yelvo marketing website

The public marketing site is a separate Vite + React 19 package in `site/`. It is
deployed by the Caddy image at `https://yelvo.vinci-labs.tech`; the operator
application remains on `https://agent.vinci-labs.tech`.

## Structure and routes

- `site/src/pages/` contains the home, privacy, imprint, and not-found page entry points.
- `site/src/components/`, `site/src/sections/`, and `site/src/styles/` hold the
  authored page UI and styling.
- `site/src/routes.tsx` maps `/`, `/privacy`, `/imprint`, and `/404` to route
  components and metadata. Unknown paths use the not-found route.
- `site/src/head.ts` renders route-specific metadata. Privacy, imprint, and
  not-found pages are marked noindex.
- `site/src/entry-server.tsx` renders pages for static prerendering; `site/src/main.tsx`
  hydrates them in the browser.
- `site/public/` contains static public files and `site/src/assets/` contains the
  Yelvo logo SVGs.

## Local development and build

Load the repository's Node version first, then run:

```sh
source ~/.nvm/nvm.sh
cd site
npm ci
npm run dev
```

The production build typechecks the app, bundles the browser and server renderers,
then writes the prerendered pages into `dist/`:

```sh
npm run build
npm run preview
npm run check:prerender
```

The prerender script writes `/`, `/privacy`, `/imprint`, and `/404` HTML files.
The check validates the final home page content and navigation links, noindex
metadata on legal pages, and output files. Its content checks are expected to
remain incomplete until the home page content is authored.

`VITE_APP_URL` optionally changes links to the operator application. It defaults
to `https://agent.vinci-labs.tech` and should be set at build time when the app
URL changes.

## Deployment

`web/Dockerfile` builds both the operator web app and this site, then places the
site in `/srv/site` in the Caddy image. The `SITE_DOMAIN` environment variable
selects the marketing-site host and defaults to `yelvo.vinci-labs.tech`.
Caddy serves route-specific prerendered HTML with no-cache headers and
fingerprinted assets with immutable caching. The site's DNS `A` record for
`yelvo.vinci-labs.tech` is already set by the owner to `188.245.155.13`.

## Content rules

Do not add testimonials, pricing, invented numbers, or compliance claims.
