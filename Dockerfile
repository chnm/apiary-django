FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv

FROM stagex/pallet-python:sx2026.06.0@sha256:8b238a3f0ee6a4d30fb5a081868d910d8b90362d33ae6674ab6d548d8c9f0fd7 AS base
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH=/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .

# Hermetic CI gate: no database or network; CI builds this target and discards it.
FROM base AS test
RUN uv sync --frozen --no-install-project
# A throwaway key long and random enough to pass check --deploy.
ENV DJANGO_SECRET_KEY=test-only-2c8f0e5b9d4a7f1e6c3b8a5d2f9e4c7b1a6d3f8e5c2b9a4d
RUN python manage.py check --deploy --fail-level WARNING \
    && python manage.py makemigrations --check --dry-run --settings apiary.settings.test \
    && pytest

FROM base AS runtime
# Empty until the shared workflow passes the triggering commit.
ARG SOURCE_SHA=""
ENV SOURCE_SHA=$SOURCE_SHA
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --no-input
USER 10001:10001
EXPOSE 8000
# The StageX base sets ENTRYPOINT to python, which would wrap the CMD below.
ENTRYPOINT []
CMD ["gunicorn", "apiary.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "8", "--max-requests", "1000", "--access-logfile", "-", "--error-logfile", "-", "--no-control-socket"]
