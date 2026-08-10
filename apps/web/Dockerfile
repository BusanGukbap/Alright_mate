# syntax=docker/dockerfile:1.12@sha256:93bfd3b68c109427185cd78b4779fc82b484b0b7618e36d0f104d4d801e66d25

ARG BUILD_VERSION=development
ARG VCS_REF=unknown

FROM node:22.12.0-alpine3.20@sha256:027911463b296bdaf6df82b5ccf2c6b290fee725d5fba6513a037ed019400625 AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts

COPY . ./
RUN npm run build

FROM nginx:1.30.4-alpine3.24-slim@sha256:ddde39c6e51f02fde7410c2e9c234cf2d0a4c7bdbbe176aeb37d8ad7ab4eb58c AS web
ARG BUILD_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="Alright, Mate? web" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/example/alright-mate"

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/index.html"]
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
