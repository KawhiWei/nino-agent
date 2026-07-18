FROM node:22-alpine AS build

WORKDIR /app

COPY web/react/package.json web/react/package-lock.json ./
RUN npm ci

COPY web/react/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY web/react/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/health || exit 1
