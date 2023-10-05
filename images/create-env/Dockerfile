# Use the exact conda, mamba versions as used in bioconda-recipes' builds.
ARG bioconda_utils_version='1.1.3'
FROM quay.io/bioconda/bioconda-utils-build-env-cos7:${bioconda_utils_version} as bioconda-build-env
RUN /opt/conda/bin/conda list \
      --export '^(conda|mamba)$' \
      | sed -n 's/=[^=]*$//p' \
      > /tmp/requirements.txt


# Use Debian instead of BusyBox base since Miniconda currently needs external zlib.
FROM quay.io/bioconda/base-glibc-debian-bash as build

WORKDIR /tmp/work
COPY --from=bioconda-build-env /tmp/requirements.txt ./
COPY install-conda print-env-activate create-env ./
RUN apt-get install -y wget && wget -o ./miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh

RUN ./install-conda ./requirements.txt /opt/create-env


FROM quay.io/bioconda/base-glibc-busybox-bash

COPY --from=build /opt/create-env /opt/create-env
# Copy (Bioconda-specific) Conda configuration created by the install-conda script.
COPY --from=build /root/.condarc /root/

RUN \
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

ENTRYPOINT [ "/opt/create-env/bin/tini", "--", "/opt/create-env/env-execute" ]
CMD [ "bash" ]
