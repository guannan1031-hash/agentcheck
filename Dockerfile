FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY requirements.lock.txt ./
RUN pip install -r requirements.lock.txt
COPY backend/ ./backend/
COPY configs/ ./configs/
COPY demo-data/ ./demo-data/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/
RUN chown -R app:app /app
USER app
EXPOSE 8878
CMD ["python", "-m", "backend.run"]
