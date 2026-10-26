# Web app

## Design system

The Yelvo design system reference lives in [`../design-system/yelvo/`](../design-system/yelvo/). Yellow is fill-only, use `--cta` for primary actions, and write interface copy in sentence case.

## Interface layouts

The inbox keeps filters in the URL and shows a selected conversation beside the loaded conversation list on wide screens; smaller screens open the existing conversation route. Assistant settings keep the selected tab when switching assistants, and the Home range controls preserve their selection in the URL. Search is client-side over loaded inbox results, while pagination continues to load additional results from the API.

## Environment variables

- `VITE_SITE_URL` sets the product-site base URL used for the privacy-policy link. It defaults to `https://yelvo.vinci-labs.tech`.
- `VITE_USE_MOCKS=1` enables the browser mock API for local development.
