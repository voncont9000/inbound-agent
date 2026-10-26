FROM node:22-alpine AS build

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM node:22-alpine AS site-build

WORKDIR /site
COPY site/package.json site/package-lock.json ./
RUN npm ci
COPY site/ ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /web/dist /srv
COPY --from=site-build /site/dist /srv/site
COPY deploy/Caddyfile /etc/caddy/Caddyfile
