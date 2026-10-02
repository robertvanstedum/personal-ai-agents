FROM python:3.12.15-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ARG INTELLIGENCE_REVISION=working-tree
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INTELLIGENCE_REVISION=$INTELLIGENCE_REVISION
WORKDIR /app
COPY . /app/intelligence_layer/
RUN python -m unittest discover -s /app/intelligence_layer/tests -q
ENTRYPOINT ["python", "-m", "intelligence_layer.boot"]
