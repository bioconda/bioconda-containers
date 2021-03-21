ARG debian_version=9.13

FROM "debian:${debian_version}-slim"

RUN apt-get update -qq \
    && \
    apt-get install --yes \
      --no-install-recommends \
      libgl1-mesa-glx \
      locales \
      openssh-client \
      procps \
    && \
    # Add en_US.UTF-8 locale.
    sed -i \
      's/^# *\(en_US.UTF-8\)/\1/' \
      /etc/locale.gen \
    && \
    locale-gen \
    && \
    # Remove "locales" package, but keep the generated locale.
    sed -i \
      's/\s*rm .*locale-archive$/: &/' \
      /var/lib/dpkg/info/locales.prerm \
    && \
    apt-get remove --yes \
      locales \
    && \
    # Remove apt package lists.
    rm -rf /var/lib/apt/lists/* \
    && \
    # Remove contents of /usr/local as downstream images overwrite those.
    find ./usr/local/ \
      -mindepth 1 -depth \
      -delete

ENV LANG=C.UTF-8
CMD [ "bash" , "-l" ]
