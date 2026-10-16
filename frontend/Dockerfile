# Production image for the frontend, in two stages.

# Stage 1: build the static site (HTML, JS, CSS) with Node.
FROM node:24-alpine AS build
WORKDIR /app
# Install dependencies in their own layer, so code changes don't reinstall them.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: serve the built files with nginx. Node isn't needed at runtime, so the final image
# is small. nginx also forwards /api to the backend (see nginx.conf).
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
