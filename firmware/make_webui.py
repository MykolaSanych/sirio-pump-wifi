"""Регенерує src/webui.h із веб-морди (gzip у PROGMEM-масив).
Запуск із каталогу firmware/:  python make_webui.py
"""
import gzip, os
SRC = os.path.join("..", "webui", "app", "index.html")
OUT = os.path.join("src", "webui.h")

gz = gzip.compress(open(SRC, "rb").read(), 9)
with open(OUT, "w") as f:
    f.write("// Згенеровано автоматично з webui/app/index.html (make_webui.py). Не редагувати.\n")
    f.write("#pragma once\n#include <pgmspace.h>\n")
    f.write("const unsigned int INDEX_HTML_GZ_LEN = %d;\n" % len(gz))
    f.write("const unsigned char INDEX_HTML_GZ[] PROGMEM = {\n")
    for i in range(0, len(gz), 16):
        f.write("  " + ",".join("0x%02x" % b for b in gz[i:i+16]) + ",\n")
    f.write("};\n")
print("webui.h: %d B HTML -> %d B gz" % (os.path.getsize(SRC), len(gz)))
