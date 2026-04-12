FROM python:3.12-slim
ARG PIPELINE
# Persist PIPELINE into the container environment so the shell CMD can
# expand it at runtime and the source-location filter gets the correct
# repo-relative path (telescopes/<name>/pipeline.py).
ENV PIPELINE=${PIPELINE}

WORKDIR /app

# Generated proto stubs — must be on PYTHONPATH so the client library
# can import events_pb2 / events_pb2_grpc.
COPY gen/python /app/gen/python
ENV PYTHONPATH=/app/gen/python

# Install the client library from local source.
COPY lib/python /app/lib/python
RUN pip install --no-cache-dir /app/lib/python

# Copy the pipeline script under its repo-relative path so that Python's
# logging.LogRecord.pathname resolves to /app/telescopes/<name>/pipeline.py.
# Stripping the /app/ prefix then yields the correct GitHub blob path.
COPY telescopes/${PIPELINE}/pipeline.py telescopes/${PIPELINE}/pipeline.py

# -u: unbuffered stdout/stderr so Docker logs are immediate.
# Shell form so ${PIPELINE} is expanded from the ENV at container start.
CMD ["sh", "-c", "python -u telescopes/${PIPELINE}/pipeline.py"]
