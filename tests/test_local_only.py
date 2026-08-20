"""The promise that nothing leaves the machine is checked, not just stated."""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"

# Hosts that may legitimately appear: the loopback model endpoint, and XML
# namespace identifiers, which are names rather than addresses anything fetches.
_ALLOWED = re.compile(
    r"127\.0\.0\.1|localhost|schemas\.openxmlformats\.org|www\.w3\.org|example\.com"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")


def _source_files() -> list[Path]:
    return [
        path for suffix in ("*.py", "*.js", "*.html", "*.css") for path in APP_DIR.rglob(suffix)
    ]


class TestNoRemoteEndpoints:
    def test_no_external_url_appears_in_application_code(self):
        offenders = []
        for path in _source_files():
            for url in _URL_RE.findall(path.read_text(encoding="utf-8")):
                if not _ALLOWED.search(url):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {url}")

        assert not offenders, "the application must not reference any remote host:\n" + "\n".join(
            offenders
        )

    def test_the_page_loads_only_its_own_assets(self):
        html = (APP_DIR / "static" / "index.html").read_text()

        for reference in re.findall(r'(?:src|href)="([^"]*)"', html):
            assert reference.startswith("/static/"), f"{reference} is not a local asset"


class TestOutboundCallsAreLoopbackOnly:
    def test_the_only_http_client_targets_the_configured_model_host(self):
        from app.config import Settings

        assert Settings().ollama_host.startswith("http://127.0.0.1")

    @pytest.mark.parametrize(
        "module",
        ["parser", "security", "extractors", "reader", "privacy", "enrich", "guards"],
    )
    def test_no_pipeline_stage_other_than_the_model_can_make_requests(self, module: str):
        source = (APP_DIR / "pipeline" / f"{module}.py").read_text()

        for library in ("httpx", "requests", "urllib.request", "socket", "aiohttp"):
            assert (
                library not in source
            ), f"{module}.py imports {library}; only the model stage may use the network"
