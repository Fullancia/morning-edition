import subprocess
from pathlib import Path

import pytest

from morning_edition import printing


class FakeRun:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return self.result


def test_ipp_uri():
    assert printing.ipp_uri("192.168.1.50") == "ipp://192.168.1.50/ipp/print"
    assert printing.ipp_uri("brother.local") == "ipp://brother.local/ipp/print"
    assert printing.ipp_uri("ipps://p.example/ipp/print") == "ipps://p.example/ipp/print"


def test_ensure_queue_runs_lpadmin_driverless():
    run = FakeRun()
    printing.CupsPrinter(run).ensure_queue("192.168.1.50")
    assert run.calls[0] == ["lpadmin", "-p", "morning-edition", "-E",
                            "-v", "ipp://192.168.1.50/ipp/print", "-m", "everywhere"]


def test_ensure_queue_errors():
    with pytest.raises(printing.PrintError, match="No printer address"):
        printing.CupsPrinter(FakeRun()).ensure_queue(" ")
    with pytest.raises(printing.PrintError, match="unable to connect"):
        printing.CupsPrinter(FakeRun(1, stderr="lpadmin: unable to connect")).ensure_queue("10.0.0.9")


def test_print_pdf_duplex_actual_size():
    run = FakeRun(stdout="request id is morning-edition-12 (1 file(s))\n")
    job = printing.CupsPrinter(run).print_pdf(Path("/data/editions/0001/edition.pdf"))
    assert job == "morning-edition-12"
    assert run.calls[0] == ["lp", "-d", "morning-edition", "-o", "sides=two-sided-long-edge",
                            "-o", "media=Letter", "-o", "print-scaling=none",
                            "/data/editions/0001/edition.pdf"]


def test_print_pdf_failure():
    with pytest.raises(printing.PrintError):
        printing.CupsPrinter(FakeRun(1, stderr="lp: printer is not accepting jobs")).print_pdf(Path("/x.pdf"))


def test_missing_binary_is_a_print_error():
    def run(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", args[0])

    with pytest.raises(printing.PrintError, match="lpadmin is not installed"):
        printing.CupsPrinter(run).ensure_queue("10.0.0.9")
    with pytest.raises(printing.PrintError, match="lp is not installed"):
        printing.CupsPrinter(run).print_pdf(Path("/x.pdf"))


def test_timeout_is_a_print_error():
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 120)

    with pytest.raises(printing.PrintError, match="timed out after 120 seconds"):
        printing.CupsPrinter(run).print_pdf(Path("/x.pdf"))


def test_other_os_error_is_a_print_error():
    def run(args, **kwargs):
        raise PermissionError("denied")

    with pytest.raises(printing.PrintError, match="could not run"):
        printing.CupsPrinter(run).ensure_queue("10.0.0.9")
