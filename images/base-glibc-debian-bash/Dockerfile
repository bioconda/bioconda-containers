ARG debian_version=10.8

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
    # On Debian 10 (and 11) libgl1-mesa-glx pulls in libgl1-mesa-dri (which in
    # turn has more heavy-weight dependencies). We leave these out of the image
    # (by manually removing it from "Depends:" list) like we do with Debian 9.
    sed -i \
      '/^Depends:/ s/, libgl1-mesa-dri\>//g' \
      /var/lib/dpkg/status \
    && \
    apt-get autoremove --yes \
    && \
    # Remove apt package lists.
    rm -rf /var/lib/apt/lists/* \
    && \
    # Remove contents of /usr/local as downstream images overwrite those.
    find ./usr/local/ \
      -mindepth 1 -depth \
      -delete

# Bash 4.* did not have default key bindings for control-arrow-key key
# combinations. Add some for convenience:
RUN >> /etc/inputrc \
      printf '%s\n' \
      '' \
      '"\e[5C": forward-word' \
      '"\e[5D": backward-word' \
      '"\e\e[C": forward-word' \
      '"\e\e[D": backward-word' \
      '"\e[1;5C": forward-word' \
      '"\e[1;5D": backward-word' \
      ;

# env-activate.sh (+ optionally env-execute) should be overwritten downstream.
# - env-activate.sh:
#    Is sourced (via symlink in /etc/profile.d/) to activate the /usr/local env.
# - env-execute:
#    Is set as the ENTRYPOINT to activate /usr/local before exec'ing CMD.
RUN touch /usr/local/env-activate.sh \
    && \
    touch /usr/local/env-execute \
    && \
    chmod +x /usr/local/env-execute \
    && \
    ln -s \
      /usr/local/env-activate.sh \
      /etc/profile.d/env-activate.sh \
    && \
    printf '%s\n' \
      '#! /bin/bash' \
      ". '/usr/local/env-activate.sh'" \
      'exec "${@}"' \
      > /usr/local/env-execute

ENV LANG=C.UTF-8
ENTRYPOINT [ "/usr/local/env-execute" ]
CMD [ "bash" ]
