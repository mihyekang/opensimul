"""HTML 구조 검증 — 중복 ID, 필수 요소 존재, CSS 속성 확인."""
import re
from pathlib import Path

import pytest

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

HTML_PATH = Path(__file__).parent.parent / "static" / "index.html"

pytestmark = pytest.mark.skipif(not BS4_AVAILABLE, reason="beautifulsoup4 not installed")


@pytest.fixture(scope="module")
def soup():
    return BeautifulSoup(HTML_PATH.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def raw_html():
    return HTML_PATH.read_text(encoding="utf-8")


class TestNoDuplicateIds:
    def test_no_duplicate_ids(self, soup):
        ids = [tag["id"] for tag in soup.find_all(id=True)]
        duplicates = [i for i in ids if ids.count(i) > 1]
        assert duplicates == [], f"중복 ID 발견: {list(set(duplicates))}"


class TestPurchaseElements:
    def test_purchase_btn_exists(self, soup):
        assert soup.find(id="purchase-btn") is not None, "#purchase-btn 이 없습니다"

    def test_purchase_overlay_exists(self, soup):
        assert soup.find(id="purchase-overlay") is not None, "#purchase-overlay 가 없습니다"

    def test_purchase_content_exists(self, soup):
        assert soup.find(id="purchase-content") is not None, "#purchase-content 가 없습니다"

    def test_purchase_btn_in_hamburger_menu(self, soup):
        hamburger = soup.find(id="hamburger-menu")
        assert hamburger is not None, "#hamburger-menu 가 없습니다"
        btn = hamburger.find(id="purchase-btn")
        assert btn is not None, "#purchase-btn 이 #hamburger-menu 안에 없습니다"

    def test_purchase_close_btn_exists(self, soup):
        assert soup.find(id="purchase-close-btn") is not None, "#purchase-close-btn 이 없습니다"

    def test_purchase_apply_btn_exists(self, soup):
        assert soup.find(id="purchase-apply-btn") is not None, "#purchase-apply-btn 이 없습니다"


class TestPurchaseOverlayCss:
    def test_purchase_overlay_zindex(self, raw_html):
        """#purchase-overlay CSS 에 z-index:500 이상이 있어야 함."""
        pattern = r"#purchase-overlay\s*\{[^}]*z-index\s*:\s*(\d+)"
        match = re.search(pattern, raw_html)
        assert match is not None, "#purchase-overlay 의 z-index CSS를 찾을 수 없음"
        z = int(match.group(1))
        assert z >= 500, f"#purchase-overlay z-index={z} < 500 (history/weekly overlay 뒤로 숨을 수 있음)"

    def test_hamburger_menu_overflow(self, raw_html):
        """#hamburger-menu 에 overflow-y:auto 와 max-height 가 있어야 함."""
        pattern = r"#hamburger-menu\s*\{([^}]*)\}"
        match = re.search(pattern, raw_html, re.DOTALL)
        assert match is not None, "#hamburger-menu CSS 블록을 찾을 수 없음"
        block = match.group(1)
        assert "overflow-y" in block and "auto" in block, \
            "#hamburger-menu 에 overflow-y:auto 가 없음 (메뉴 항목이 잘릴 수 있음)"
        assert "max-height" in block, \
            "#hamburger-menu 에 max-height 가 없음 (메뉴 항목이 화면 아래로 넘칠 수 있음)"
