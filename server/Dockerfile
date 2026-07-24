FROM node:22-alpine AS web-build
WORKDIR /src/web-ui
COPY web-ui/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web-ui/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/server
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ /app/server/
COPY --from=web-build /src/web-ui/dist /app/web-ui/dist
RUN useradd --system --uid 10001 --create-home wcm \
    && mkdir -p /app/server/data /app/server/updates /app/server/logs \
    && chown -R wcm:wcm /app
USER wcm
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
