"""Pull the stock WiNET module's web files (pages + referenced JS/CSS) so we can
study and redesign them. Read-only GETs. Saves under webui/stock/.
"""
import os
import re
import urllib.parse
import urllib.request

BASE = "http://192.168.0.107"
OUT = os.path.join(os.path.dirname(__file__), "webui", "stock")
SEED = ["/", "/status.html", "/management.html", "/user.html", "/index.html"]
REF = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.I)


def localpath(path):
    p = path.split("?")[0].split("#")[0]
    if p in ("", "/"):
        p = "/index.html"
    return os.path.join(OUT, p.lstrip("/"))


def main():
    os.makedirs(OUT, exist_ok=True)
    seen, queue = set(), list(SEED)
    while queue:
        path = queue.pop(0)
        key = path.split("?")[0].split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        url = BASE + path
        try:
            r = urllib.request.urlopen(url, timeout=6)
            body = r.read()
            ct = r.headers.get("Content-Type", "")
            acao = r.headers.get("Access-Control-Allow-Origin")
            fp = localpath(path)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(body)
            print(f"{r.status}  {len(body):>6}B  CORS={acao}  {ct:<24} {path}")
            if "html" in ct or "javascript" in ct or path.endswith((".html", ".js")):
                text = body.decode("utf-8", "replace")
                for m in REF.findall(text):
                    if m.startswith(("http://", "https://", "//", "data:", "mailto:", "#", "javascript:")):
                        continue
                    ref = urllib.parse.urljoin(path, m)
                    if ref.split("?")[0] not in seen:
                        queue.append(ref)
        except Exception as e:
            print(f"ERR  {path}: {e}")
    print(f"\nsaved under {OUT}")


if __name__ == "__main__":
    main()
