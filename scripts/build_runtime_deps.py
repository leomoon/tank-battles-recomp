#!/usr/bin/env python3
"""Build the runtime's third-party dependencies as static libraries with the staged Zig.

  python scripts/build_runtime_deps.py --toolchain DIR --cache DIR --target linux|windows --prefix DIR [--work DIR]

Sources are the pinned, SHA-256-verified archives in ci/toolchain/manifest.json
(fetch them with `scripts/toolchain.py fetch --sources`). Every library is
compiled by the staged Zig for the requested target, so the runtime bundle and
everything it links against share one compiler and one C++ standard library
(libc++), and the standalone builders can link players without any installed
development tools.

Linux: SDL2 loads X11, PulseAudio, ALSA and udev at run time but needs their
headers to build. They come from --system-headers (default: the build
machine's /usr/include), copied into a private include directory; glibc
headers always come from Zig's own glibc 2.31 baseline. SDL2's native Wayland
backend is not built: Wayland desktops run the player through XWayland.

This runs on a Linux CI machine for both targets. It needs Perl and make for
OpenSSL and libiconv; those are build-machine tools, never shipped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from toolchain import TARGETS, cached, load_manifest, manifest_digest, safe_extract, verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LINUX_HEADER_SETS = ("X11", "pulse", "alsa", "libudev.h")


def run(command, cwd=None, env=None):
    print("[deps] $ " + " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], cwd=cwd, env=env, check=True)


class Builder:
    def __init__(self, toolchain, cache, target, prefix, work, system_headers):
        self.toolchain = Path(toolchain).resolve()
        verify(self.toolchain)
        self.cache = Path(cache)
        self.target = target
        self.triple = TARGETS[target]
        self.prefix = Path(prefix).resolve()
        self.work = Path(work).resolve()
        self.manifest = load_manifest()
        exe = ".exe" if os.name == "nt" else ""
        self.cmake = self.toolchain / "cmake/bin" / f"cmake{exe}"
        self.ninja = self.toolchain / f"ninja{exe}"
        self.toolchain_file = self.toolchain / "cmake" / f"zig-{target}.cmake"
        self.headers = None
        if target == "linux":
            self.headers = self.work / "system-headers"
            if self.headers.exists():
                shutil.rmtree(self.headers)
            self.headers.mkdir(parents=True)
            for name in LINUX_HEADER_SETS:
                source = Path(system_headers) / name
                if not source.exists():
                    raise ValueError(f"Missing build-machine header set {source}; install the X11, PulseAudio, ALSA and udev development headers")
                (shutil.copytree if source.is_dir() else shutil.copy2)(source, self.headers / name)
        self.env = dict(os.environ)
        self.env["ZIG_GLOBAL_CACHE_DIR"] = str(self.work / "zig-cache")
        self.env["ZIG_LOCAL_CACHE_DIR"] = str(self.work / "zig-cache")

    def source(self, name):
        entry = self.manifest["sources"][name]
        archive = cached(self.cache, entry["sha256"])
        if not archive.is_file():
            raise ValueError(f"Missing verified source {name}; run scripts/toolchain.py fetch --sources")
        dest = self.work / "src" / name
        if not dest.exists():
            safe_extract(archive, dest)
        return dest

    def cflags(self):
        flags = ["-O2", "-g0", "-fPIC"] if self.target == "linux" else ["-O2", "-g0"]
        if self.headers:
            flags.append(f"-idirafter{self.headers}")
        return " ".join(flags)

    def cmake_project(self, name, options, source_subdir="", system_libraries=False):
        source = self.source(name) / source_subdir
        build = self.work / "build" / name
        # SDL2 reads the sonames of the system libraries it loads at run time
        # (X11, ALSA, PulseAudio, udev) and never links them; only for it may
        # find_library see the build machine. Headers and glibc stay Zig's.
        roots = f"{self.prefix};/" if system_libraries else str(self.prefix)
        run([self.cmake, "-S", source, "-B", build, "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={self.ninja}", f"-DCMAKE_TOOLCHAIN_FILE={self.toolchain_file}",
             "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={self.prefix}",
             f"-DCMAKE_PREFIX_PATH={self.prefix}", f"-DCMAKE_FIND_ROOT_PATH={roots}",
             f"-DCMAKE_C_FLAGS={self.cflags()}", f"-DCMAKE_CXX_FLAGS={self.cflags()}",
             "-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON", *options], env=self.env)
        run([self.cmake, "--build", build, "--parallel", str(os.cpu_count() or 4)], env=self.env)
        run([self.cmake, "--install", build], env=self.env)

    def compiler(self):
        # Autoconf and OpenSSL's Configure split CC on spaces and cannot take quotes.
        wrapper = self.toolchain / "bin" / "zig-cc"
        if " " in str(wrapper):
            raise ValueError("Stage the toolchain in a path without spaces to build the dependencies")
        return f"{wrapper} -target {self.triple}"

    def openssl(self):
        source = self.source("openssl")
        platform = "linux-x86_64" if self.target == "linux" else "mingw64"
        env = dict(self.env, CC=self.compiler(), AR=str(self.toolchain / "bin/zig-ar"),
                   RANLIB=str(self.toolchain / "bin/zig-ranlib"), RC=str(self.toolchain / "bin/zig-rc"))
        # /etc/ssl is the Debian/Ubuntu/Arch/Fedora location of the system trust store.
        openssldir = "--openssldir=/etc/ssl" if self.target == "linux" else ""
        run(["perl", "Configure", platform, "no-shared", "no-tests", "no-docs", "no-apps", "no-module",
             f"--prefix={self.prefix}", "--libdir=lib", *([openssldir] if openssldir else []), self.cflags()],
            cwd=source, env=env)
        run(["make", f"-j{os.cpu_count() or 4}", "build_libs"], cwd=source, env=env)
        run(["make", "install_dev"], cwd=source, env=env)

    def libiconv(self):
        source = self.source("libiconv")
        env = dict(self.env, CC=self.compiler(), AR=str(self.toolchain / "bin/zig-ar"),
                   RANLIB=str(self.toolchain / "bin/zig-ranlib"), RC=str(self.toolchain / "bin/zig-rc"),
                   CFLAGS="-O2")
        run(["./configure", "--host=x86_64-w64-mingw32", f"--prefix={self.prefix}",
             "--enable-static", "--disable-shared", "--disable-nls"], cwd=source, env=env)
        # The Windows version resource uses windres syntax and only versions the
        # DLL, which is not built; Zig's resource compiler takes rc.exe syntax.
        no_resource = "OBJECTS_RES_yes="
        run(["make", f"-j{os.cpu_count() or 4}", no_resource], cwd=source, env=env)
        run(["make", "install", no_resource], cwd=source, env=env)

    def drop_spirv_tools_shared(self):
        """SPIRV-Tools always builds and installs a shared library the runtime never
        links. Remove it and its import entry so the package stays static-only
        and loads without it (CMake checks every imported file exists)."""
        for path in [*(self.prefix / "lib").glob("libSPIRV-Tools-shared*"), *(self.prefix / "bin").glob("libSPIRV-Tools-shared*")]:
            path.unlink()
        release = self.prefix / "lib/cmake/SPIRV-Tools/SPIRV-ToolsTarget-release.cmake"
        lines, skipping = [], False
        for line in release.read_text().splitlines(keepends=True):
            if line.startswith('# Import target "SPIRV-Tools-shared"'):
                skipping = True
            if skipping:
                if not line.strip():
                    skipping = False
                continue
            if "SPIRV-Tools-shared" in line and "_cmake_import_check" in line:
                continue
            lines.append(line)
        text = "".join(lines)
        if "libSPIRV-Tools-shared" in text:
            raise ValueError("Unexpected SPIRV-Tools export layout; the shared library entry was not removed")
        release.write_text(text)

    def require_sdl_backends(self):
        """Fail rather than ship an SDL without a window or sound backend."""
        config = (self.prefix / "include/SDL2/SDL_config.h").read_text()
        needed = ("SDL_VIDEO_DRIVER_X11 1", "SDL_VIDEO_DRIVER_X11_DYNAMIC ", "SDL_AUDIO_DRIVER_PULSEAUDIO 1",
                  "SDL_AUDIO_DRIVER_ALSA 1", "SDL_JOYSTICK_LINUX 1", "HAVE_LIBUDEV_H 1", "SDL_UDEV_DYNAMIC ",
                  "SDL_VIDEO_VULKAN 1")
        missing = [item.split()[0] for item in needed if f"#define {item}" not in config]
        if missing:
            raise ValueError("SDL2 was configured without required backends: " + ", ".join(missing))

    def build_all(self):
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.cmake_project("vulkan_headers", ["-DVULKAN_HEADERS_ENABLE_TESTS=OFF", "-DVULKAN_HEADERS_ENABLE_MODULE=OFF"])
        self.cmake_project("spirv_headers", ["-DSPIRV_HEADERS_ENABLE_TESTS=OFF"])
        self.cmake_project("spirv_tools", ["-DSPIRV-Headers_SOURCE_DIR=" + str(self.source("spirv_headers")),
                                           "-DSPIRV_SKIP_TESTS=ON", "-DSPIRV_SKIP_EXECUTABLES=ON",
                                           "-DSPIRV_WERROR=OFF", "-DSPIRV_TOOLS_BUILD_STATIC=ON"])
        self.drop_spirv_tools_shared()
        self.cmake_project("glslang", ["-DENABLE_OPT=ON", "-DALLOW_EXTERNAL_SPIRV_TOOLS=ON", "-DGLSLANG_TESTS=OFF",
                                       "-DENABLE_GLSLANG_BINARIES=OFF", "-DENABLE_HLSL=ON", "-DGLSLANG_ENABLE_INSTALL=ON"])
        sdl = ["-DSDL_SHARED=OFF", "-DSDL_STATIC=ON", "-DSDL_TEST=OFF", "-DSDL_STATIC_PIC=ON"]
        if self.target == "linux":
            sdl += ["-DSDL_WAYLAND=OFF", "-DSDL_KMSDRM=OFF", "-DSDL_JACK=OFF", "-DSDL_PIPEWIRE=OFF",
                    "-DSDL_SNDIO=OFF", "-DSDL_ESD=OFF", "-DSDL_ARTS=OFF", "-DSDL_NAS=OFF", "-DSDL_DBUS=OFF",
                    "-DSDL_IBUS=OFF", "-DSDL_FCITX=OFF", "-DSDL_X11=ON", "-DSDL_X11_SHARED=ON",
                    "-DSDL_PULSEAUDIO=ON", "-DSDL_PULSEAUDIO_SHARED=ON", "-DSDL_ALSA=ON", "-DSDL_ALSA_SHARED=ON",
                    "-DSDL_LIBUDEV=ON", "-DSDL_VULKAN=ON", "-DSDL_OPENGL=OFF", "-DSDL_OPENGLES=OFF"]
        self.cmake_project("sdl2", sdl, system_libraries=self.target == "linux")
        if self.target == "linux":
            self.require_sdl_backends()
        if self.target == "windows":
            self.libiconv()
        self.openssl()
        self.cmake_project("curl", ["-DBUILD_CURL_EXE=OFF", "-DBUILD_TESTING=OFF", "-DBUILD_STATIC_LIBS=ON",
                                    "-DCURL_USE_OPENSSL=ON", "-DCURL_USE_LIBPSL=OFF", "-DCURL_USE_LIBSSH2=OFF",
                                    "-DUSE_NGHTTP2=OFF", "-DUSE_LIBIDN2=OFF", "-DCURL_ZLIB=OFF", "-DCURL_BROTLI=OFF",
                                    "-DCURL_ZSTD=OFF", "-DENABLE_ARES=OFF", "-DCURL_DISABLE_LDAP=ON",
                                    "-DOPENSSL_USE_STATIC_LIBS=ON", f"-DOPENSSL_ROOT_DIR={self.prefix}"])
        identity = {"schema": 1, "target": self.target, "triple": self.triple,
                    "toolchain": json.loads((self.toolchain / "toolchain.json").read_text())["components"]["zig"],
                    "manifest_sha256": manifest_digest(),
                    "sources": {name: {"version": entry["version"], "sha256": entry["sha256"], "licence": entry["licence"]}
                                for name, entry in self.manifest["sources"].items()
                                if self.target in entry.get("targets", ("linux", "windows"))}}
        (self.prefix / "deps.json").write_text(json.dumps(identity, indent=2) + "\n")
        print(f"[deps] {self.target} dependencies installed in {self.prefix}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--work", help="Scratch directory (default: next to the prefix)")
    parser.add_argument("--system-headers", default="/usr/include")
    args = parser.parse_args()
    work = args.work or str(Path(args.prefix).resolve().parent / f"deps-work-{args.target}")
    try:
        Builder(args.toolchain, args.cache, args.target, args.prefix, work, args.system_headers).build_all()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
