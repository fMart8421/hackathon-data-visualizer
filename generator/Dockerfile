# The generator runs as a Compose one-off (DEC-09), so no Python is needed on
# the host and the demo machine gets the same interpreter as the dev machine.
#
# Dependencies are installed from pyproject at build time; compose bind-mounts
# the source over /app and PYTHONPATH puts it ahead of site-packages, so edits
# take effect without a rebuild.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[test]"

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "-m", "cli"]
