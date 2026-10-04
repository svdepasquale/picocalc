# import go -> opens the launcher. Frozen firmwares (like ClockworkPi's)
# run their own main.py at boot and ignore one on the filesystem, so this
# is the short way in. It drops itself from sys.modules: `import go`
# works again after leaving the menu.
import sys

import menu

try:
    menu.run()
finally:
    del sys.modules["go"]
