# Builds the web app and serves it with nginx, which also passes /api and /uploads to the API.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npx vite build   # type-checking happens in development (npm run build)

FROM nginx:1.27-alpine
COPY --from=build /src/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
