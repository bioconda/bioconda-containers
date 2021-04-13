# Changelog


## bioconda/create-env 2.0.0 (2021-04-13)

### Changed

- Rename `--remove-files` to `--remove-paths`

- Replace `--strip` by `--strip-files=GLOB`

- Replace `CONDA_ALWAYS_COPY=1` usage by config option


## bioconda/create-env 1.2.1 (2021-04-09)

### Fixed

- Fail `--strip` if `strip` is not available

### Changed

- Delete links/dirs for `--remove-files`


## bioconda/create-env 1.2.0 (2021-03-30)

### Added

- Add license copying

- Add status messages

- Add help texts

### Changed

- Suppress `bash -i` ioctl warning


## bioconda/create-env 1.1.1 (2021-03-27)

### Changed

- Use `CONDA_ALWAYS_COPY=1`


## bioconda/create-env 1.1.0 (2021-03-27)

### Added

- Add option to change `create --copy`

### Changed

- Rebuild with `python` pinned to `3.8`

  To avoid hitting
    - https://github.com/conda/conda/issues/10490
    - https://bugs.python.org/issue43517


## bioconda/create-env 1.0.2 (2021-03-22)

### Changed

- Rebuild on new Debian 10 base images


## bioconda/create-env 1.0.1 (2021-03-22)

### Fixed

- Use entrypoint from `/opt/create-env/`

  `/usr/local` gets "overwritten" (=bind-mounted) when building via mulled.


## bioconda/create-env 1.0.0 (2021-03-21)

### Added

- Initial release


<!--

## bioconda/create-env X.Y.Z (YYYY-MM-DD)

### Added

- item

### Fixed

- item

### Changed

- item

### Removed

- item

-->
