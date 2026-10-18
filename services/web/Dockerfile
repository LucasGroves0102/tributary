# Build from the repo root: docker build -f services/web/Dockerfile .
FROM node:22.12-alpine AS build
WORKDIR /app
COPY services/web/package.json services/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY services/web/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY services/web/deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
