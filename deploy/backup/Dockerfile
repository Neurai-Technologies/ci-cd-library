# The pinned postgres image guarantees pg_dump matches the server; the alpine
# variant ships only libssl, not the openssl CLI the backup script encrypts
# with, so it's added here rather than hoped for at runtime (the rootfs is
# read-only, so `apk add` in the container is not an option either).
# POSTGRES_IMAGE is an exact version tag of the database server's major
# (pg_dump refuses a newer server); the compose file passes it as a build
# arg, equal to POSTGRES_IMAGE in product.settings.
ARG POSTGRES_IMAGE=postgres:16.15-alpine
FROM ${POSTGRES_IMAGE}
RUN apk add --no-cache openssl curl
