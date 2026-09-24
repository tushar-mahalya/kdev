from importlib.metadata import PackageNotFoundError, version

try:
    # The distribution's name, not the module's: PyPI does not allow "kdev".
    __version__ = version("kdev-cli")
except PackageNotFoundError:  # running from a source tree, not an install
    __version__ = "0.0.0+source"
