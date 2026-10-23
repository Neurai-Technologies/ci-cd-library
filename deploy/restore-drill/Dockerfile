# The postgres image production pins, with the init script production mounts
# from its own clone baked in. run.sh copies the product's POSTGRES_INIT_DIR
# into .init/ here before the build, so it is the real directory. Keep
# POSTGRES_IMAGE in step with the postgres service in the product's
# docker-compose.yml.
ARG POSTGRES_IMAGE=postgres:16.15-alpine
FROM ${POSTGRES_IMAGE}
COPY .init/ /docker-entrypoint-initdb.d/
