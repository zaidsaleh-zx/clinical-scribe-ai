"""Cross-check: every getElementById(...) referenced in app.js must exist in index.html."""
import re

js = open("frontend/app.js", encoding="utf-8").read()
html = open("frontend/index.html", encoding="utf-8").read()

html_ids = set(re.findall(r'id="([^"]+)"', html))
refs = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", js))
missing = sorted(r for r in refs if r not in html_ids)

print(f"referenced ids: {len(refs)}")
print("MISSING from HTML:", missing if missing else "NONE")
