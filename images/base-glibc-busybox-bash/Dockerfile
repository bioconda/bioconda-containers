ARG busybox_version=1.32.1
ARG debian_version=9

# Don't use Debian's busybox package since it only provides a smaller subset of
# BusyBox's functions (e.g., no administrative tools like adduser etc.).
# Since we create a glibc image anyway, we can also use "busybox:glibc" as the
# base image, use a the slightly smaller dynamically linked binary and reuse
# base files (e.g., /etc/passwd) from that image.

FROM "busybox:${busybox_version}-glibc" AS target_base
FROM "debian:${debian_version}-slim" AS build_base


# Build busybox ourselves to have more fine-grained control over what we want
# (or not want) to include.
# Use old Debian version to ensure compatible (low glibc requirement) binaries.
FROM debian:9-slim AS busybox_builder
RUN apt-get update && \
    apt-get install -y \
      bzip2 curl ca-certificates tar gcc gnupg dirmngr make
COPY build-busybox /usr/local/bin
ARG busybox_version
RUN build-busybox "${busybox_version}" /busybox


FROM build_base AS rootfs_builder

WORKDIR /rootfs
COPY --from=target_base / ./

RUN find . -samefile ./bin/busybox -delete
COPY --from=busybox_builder /busybox/busybox ./
RUN mkdir -p \
      ./bin ./usr/bin ./usr/local/bin \
      ./sbin ./usr/sbin ./usr/local/sbin \
    && \
    chroot . /busybox --install \
    && \
    # Somehow (container layers?) busybox does not have the same inode if it's
    # directly put at /bin/busybox => hardlink it manually afterwards.
    rm ./busybox && ln ./bin/ln ./bin/busybox

# Remove glibc files. They are incomplete and get substituted by `install-pkgs`.
RUN rm -rf ./lib ./lib64

# Install helper tools used by install-pkgs.
RUN apt-get update -qq \
    && \
    apt-get install --yes --no-install-recommends \
      patchelf

COPY install-pkgs /usr/local/bin
RUN install-pkgs "$( pwd )" /tmp/work bash ncurses-base libc-bin

FROM scratch
COPY --from=rootfs_builder /rootfs /
ENV LANG=C.UTF-8
CMD [ "bash" , "-l" ]
