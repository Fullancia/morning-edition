import re
import subprocess
from collections.abc import Callable
from pathlib import Path

QUEUE = "morning-edition"


class PrintError(Exception):
    pass


def ipp_uri(address: str) -> str:
    address = address.strip()
    if address.startswith(("ipp://", "ipps://")):
        return address
    return f"ipp://{address}/ipp/print"


class CupsPrinter:
    """Driverless printing through the CUPS daemon running in the container."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.run = run

    def _call(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            result = self.run(args, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as exc:
            raise PrintError(f"{args[0]} is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise PrintError(f"{args[0]} timed out after {exc.timeout:g} seconds") from exc
        except OSError as exc:
            raise PrintError(f"{args[0]} could not run: {exc}") from exc
        if result.returncode != 0:
            raise PrintError(result.stderr.strip() or f"{args[0]} failed")
        return result

    def ensure_queue(self, address: str) -> None:
        if not address.strip():
            raise PrintError("No printer address set")
        self._call(["lpadmin", "-p", QUEUE, "-E", "-v", ipp_uri(address), "-m", "everywhere"])

    def print_pdf(self, path: Path) -> str:
        result = self._call(["lp", "-d", QUEUE, "-o", "sides=two-sided-long-edge", "-o", "media=Letter",
                             "-o", "print-scaling=none", str(path)])
        match = re.search(r"request id is (\S+)", result.stdout)
        return match.group(1) if match else result.stdout.strip()
