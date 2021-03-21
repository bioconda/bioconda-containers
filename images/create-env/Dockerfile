# Use Debian instead of BusyBox base since Miniconda currently needs external zlib.
FROM bioconda/base-glibc-debian-bash as build

WORKDIR /tmp/work
COPY install-conda print-env-activate create-env ./
ADD https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh ./miniconda.sh

ARG version='4.9.2'
RUN ./install-conda "${version}" /opt/create-env


FROM bioconda/base-glibc-busybox-bash

COPY --from=build /opt/create-env /opt/create-env
# Copy (Bioconda-specific) Conda configuration created by the install-conda script.
COPY --from=build /root/.condarc /root/

RUN cp /opt/create-env/env-activate.sh /usr/local/ \
    && \
    # Use a per-user config (instead of conda config --sys) for more flexibility.
    cp /root/.condarc /etc/skel/ \
    && \
    # Enable conda shell function for login shells.
    ln -s /opt/create-env/etc/profile.d/conda.sh /etc/profile.d/ \
    && \
    # Enable conda function in interactive Bash (via .bashrc) and POSIX shells (via ENV).
    printf '%s\n' \
      '\. /etc/profile.d/conda.sh' \
      | tee -a /root/.bashrc \
      >>   /etc/skel/.bashrc
ENV ENV=/etc/profile.d/conda.sh

ENTRYPOINT [ "/opt/create-env/bin/tini", "--", "/usr/local/env-execute" ]
CMD [ "bash" ]
