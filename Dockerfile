FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv
FROM python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH=/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
COPY . .

FROM base AS test
ENV DJANGO_SECRET_KEY=test-only-2c8f0e5b9d4a7f1e6c3b8a5d2f9e4c7b1a6d3f8e5c2b9a4d
RUN python manage.py check --deploy --fail-level WARNING \
    && python manage.py test apiary.test_deployment --noinput

FROM base AS runtime
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "apiary.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--max-requests", "1000", "--access-logfile", "-", "--error-logfile", "-"]
