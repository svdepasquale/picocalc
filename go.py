# import go -> opens the launcher. Frozen firmwares (like ClockworkPi's)
# run their own main.py at boot and ignore one on the filesystem, so this
# is the short way in. It drops itself from sys.modules: `import go`
# works again after leaving the menu. Executed rather than imported
# (`mpremote run go.py`, r in Files) it was never there: pop, not del.
import sys

import menu

try:
    menu.run()
finally:
    sys.modules.pop("go", None)
