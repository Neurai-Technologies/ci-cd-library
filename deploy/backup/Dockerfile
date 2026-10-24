# The pinned postgres image guarantees pg_dump matches the server; the alpine
# variant ships only libssl, not the openssl CLI the backup script encrypts
# with, so it's added here rather than hoped for at runtime (the rootfs is
# read-only, so `apk add` in the container is not an option either).
FROM postgres:18.4-alpine
RUN apk add --no-cache openssl curl
