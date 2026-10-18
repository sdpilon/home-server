FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge ./bridge
COPY entrypoint.sh /usr/local/bin/chatbridge-entrypoint
RUN chmod +x /usr/local/bin/chatbridge-entrypoint

# Match the host user (uid/gid 1000) so the bind-mounted ./data stays writable
# by the same account that owns the rest of /app/data/projects. /data is created
# here too, so a missing volume mount fails with a clear message rather than a
# permission error on the container root.
RUN groupadd -g 1000 bridge && \
    useradd -u 1000 -g 1000 -m -s /usr/sbin/nologin bridge && \
    mkdir -p /data && \
    chown -R 1000:1000 /data
USER 1000:1000

ENTRYPOINT ["chatbridge-entrypoint"]
CMD ["serve"]
