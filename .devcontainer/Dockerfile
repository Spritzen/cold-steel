FROM archlinux:latest

# The container user mirrors the host user (name, uid, home) so that every
# host path -- Steam library, Paradox user data, this workspace -- is mounted
# at the SAME absolute path inside. Game discovery reads libraryfolders.vdf and
# XDG dirs, which hold host paths; keeping them identical means the manager
# behaves the same in here as it will on the host.
ARG USERNAME=odin
ARG USER_UID=1000
ARG USER_GID=1000

# Base toolchain.
#   base-devel/git/github-cli  build + VCS
#   nodejs/npm                 VS Code extensions, Claude Code fallback install
#   python + yaml/pillow, imagemagick
#                              run the stellaris framework tools (tools/*.py)
#   sqlite                     inspect launcher-v2.sqlite (mod registry + playsets)
#   wayland/libxkbcommon/mesa/fontconfig/ttf-dejavu
#                              runtime for showing the GUI on the host display
RUN pacman -Syu --noconfirm --needed \
        base-devel \
        git \
        github-cli \
        openssh \
        sudo \
        which \
        less \
        file \
        zip \
        unzip \
        ripgrep \
        jq \
        sqlite \
        fish \
        nodejs \
        npm \
        python \
        python-pip \
        python-yaml \
        python-pillow \
        imagemagick \
        wayland \
        libxkbcommon \
        mesa \
        fontconfig \
        ttf-dejavu \
    && pacman -Scc --noconfirm

# Application stack: Python + Qt 6, all from Arch's official repos -- no venv,
# no pip. Developing against exactly what users get from pacman keeps the
# eventual PKGBUILD's depends= honest.
#   Runtime (become PKGBUILD depends=):
#     pyside6, qt6-wayland   GUI toolkit + native Wayland backend
#     python-msgspec         typed structs + fast JSON/msgpack for the parse cache
#     python-xxhash          fast content hashing for change detection
#   Dev only (makedepends= / checkdepends=):
#     python-pytest, python-pytest-qt, python-pytest-benchmark
#                            tests, GUI tests, and timing regressions
#     ruff                   lint + format
#     mypy                   type checking; also provides mypyc for compiling
#                            hot modules later if profiling says so
RUN pacman -S --noconfirm --needed \
        pyside6 \
        qt6-wayland \
        python-msgspec \
        python-xxhash \
        python-pytest \
        python-pytest-qt \
        python-pytest-benchmark \
        ruff \
        mypy \
    && pacman -Scc --noconfirm

RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}" \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 "/etc/sudoers.d/${USERNAME}"

# Mount points for binds that land inside $HOME, plus the Wayland runtime dir.
# Created up front so ownership is the user's, not root's.
RUN mkdir -p \
        "/home/${USERNAME}/.claude" \
        "/home/${USERNAME}/.config/gh" \
        "/home/${USERNAME}/.local/share/Steam" \
        "/home/${USERNAME}/.local/share/Paradox Interactive" \
    && chown -R "${USER_UID}:${USER_GID}" "/home/${USERNAME}" \
    && install -d -m 0700 -o "${USER_UID}" -g "${USER_GID}" "/run/user/${USER_UID}"

USER ${USERNAME}
