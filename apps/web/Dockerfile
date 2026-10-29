FROM node:22-alpine AS deps
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app/apps/web
COPY --from=deps /app/apps/web/node_modules ./node_modules
COPY apps/web ./
ARG VITE_MARGIN_API_BASE_URL
ARG VITE_OIDC_ISSUER
ARG VITE_OIDC_CLIENT_ID
ARG VITE_AUTH_AUTO_REDIRECT
ENV VITE_MARGIN_API_BASE_URL=${VITE_MARGIN_API_BASE_URL}
ENV VITE_OIDC_ISSUER=${VITE_OIDC_ISSUER}
ENV VITE_OIDC_CLIENT_ID=${VITE_OIDC_CLIENT_ID}
ENV VITE_AUTH_AUTO_REDIRECT=${VITE_AUTH_AUTO_REDIRECT}
RUN npm run build

FROM nginx:1.27-alpine AS runner
COPY --from=builder /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
