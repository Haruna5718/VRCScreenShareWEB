FROM node:22-alpine AS web-build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	DATA_DIR=/data \
	PORT=8000
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ffmpeg ca-certificates \
	&& rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py ./server.py
COPY --from=web-build /web/dist ./dist
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "server.py"]
