# US9.7: a simple built container (not the hot-reload vite dev server --
# iterate locally via `npm run dev` as before). Default
# VITE_API_BASE_URL fallback in src/api/client.ts (http://127.0.0.1:8000)
# already matches the api service's host-published port, so the browser
# (running on the host, not inside this container) reaches it correctly
# with no build-arg plumbing needed for the default local setup.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
