FROM alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

RUN apk add --no-cache \
      postgresql18-client=18.4-r0 \
      age=1.3.1-r5 \
      docker-cli=29.5.3-r0 \
      jq=1.8.1-r0 \
      rclone=1.74.1-r1 \
    && addgroup -g 10001 ops \
    && adduser -D -H -u 10001 -G ops ops

COPY --chmod=0555 backup.sh restore-drill.sh /usr/local/bin/

ENV HOME=/home/ops
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/backup.sh"]
