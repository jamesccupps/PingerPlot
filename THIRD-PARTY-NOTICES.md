# Third-party notices

PingerPlot has no third-party Python dependencies. The Windows executables (`PingerPlot.exe` and
`pingerplot-headless.exe`) bundle a Python runtime and the parts of its standard library the app uses; running from
source uses your own installation of them. The app's own code is under the MIT license in [LICENSE](LICENSE).

| Component | What it's for | License |
|---|---|---|
| [Python](https://www.python.org/) 3.12 and its standard library | the runtime | [PSF License 2.0](https://docs.python.org/3/license.html) |
| [Tcl/Tk](https://www.tcl.tk/) 8.6 (tkinter) | the window and its widgets (`PingerPlot.exe` only) | [Tcl/Tk license](https://www.tcl.tk/software/tcltk/license.html) (BSD-style) |
| [OpenSSL](https://www.openssl.org/) 3, as built into Python | HTTPS, for the Map tab's lookups and webhooks | [Apache 2.0](https://www.openssl.org/source/license.html) |
| [libffi](https://sourceware.org/libffi/), as built into Python | ctypes (the Windows ICMP API) | MIT |
| [PyInstaller](https://pyinstaller.org/) bootloader | starts the bundled Python | GPL 2.0 with the bootloader exception, which allows distributing it with any program |

The world map's coastlines are [Natural Earth](https://www.naturalearthdata.com/) 1:110m data, simplified and kept in
`pingerplot/worldmap.py`. Natural Earth is in the public domain.

Where routers are and whose network they're on comes from [ipwho.is](https://ipwho.is/), a free web service:
nothing of it is bundled, only public router addresses are sent, and Settings turns it off (see the README's
*Updates and privacy*).

Building the executables uses [PyInstaller](https://pyinstaller.org/) and its hooks, and the tests use
[pytest](https://pytest.org/), at the versions pinned in `packaging/requirements-build.txt`; neither is part of the
app. The app icon is drawn by `pingerplot/appicon.py`.
