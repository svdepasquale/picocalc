# picocalc-app: Hello | Sample: copy and edit me
# Any .py in /sd/apps (or /apps on flash) shows up in the menu's Apps list
# and runs as __main__. The toolkit's helpers import as usual.
from pico_utils import BGREEN, GREY, paint, screen_header, status_text

screen_header("Hello")
print(paint("Hello from the SD card!", BGREEN))
print("")
print("Status: " + (status_text() or "clock not set"))
print("")
print(paint("Edit me: Files > sd > apps > hello.py > e", GREY))
