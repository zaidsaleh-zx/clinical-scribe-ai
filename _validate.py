"""Quick structural validation for clinical-scribe-ai (dev use only)."""
import io
from html.parser import HTMLParser


def check_css():
    css = io.open(r"frontend/style.css", encoding="utf-8").read()
    print("CSS length:", len(css))
    print("CSS braces {:", css.count("{"))
    print("CSS braces }:", css.count("}"))
    print("CSS parens (:", css.count("("))
    print("CSS parens ):", css.count(")"))


class HTMLValidator(HTMLParser):
    VOID = {
        "meta", "link", "input", "br", "hr", "img", "source", "area",
        "base", "col", "embed", "param", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"stray closing </{tag}>")
        elif self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"mismatch </{tag}> (stack top = {self.stack[-1]})")


def check_html():
    html = io.open(r"frontend/index.html", encoding="utf-8").read()
    p = HTMLValidator()
    p.feed(html)
    print("HTML unclosed at EOF:", p.stack)
    print("HTML errors:", p.errors)


if __name__ == "__main__":
    check_css()
    check_html()