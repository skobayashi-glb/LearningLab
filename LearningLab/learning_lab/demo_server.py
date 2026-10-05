"""Reference protocol server for adapter integration tests."""
import sys

from .demo import NavigationDemo
from .rpc import EnvironmentServer


if __name__ == "__main__":
    EnvironmentServer(NavigationDemo()).serve(sys.stdin, sys.stdout)
