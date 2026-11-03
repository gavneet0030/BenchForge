"""BenchForge performance benchmarking and regression analysis toolkit."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("benchforge")
except PackageNotFoundError:
    __version__ = "0+unknown"
