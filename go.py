# import go -> opens the launcher. Frozen firmwares (like ClockworkPi's)
# run their own main.py at boot and ignore one on the filesystem, so this
# is the short way in. It drops itself from sys.modules: `import go`
# works again after leaving the menu. Executed rather than imported
# (`mpremote run go.py`, r in Files) it was never there: pop, not del.
# tools/deploy.sh sends the toolkit as .mpy files built for this firmware's
# bytecode: after a firmware update they may no longer load, and this file
# (sent as source) says so instead of failing at every power-on.
import sys

try:
    import menu
except ValueError as error:  # "incompatible .mpy file"
    menu = None
    print("Toolkit not loadable:", error)
    print("Run tools/deploy.sh again (or --py) from the computer.")

try:
    if menu is not None:
        menu.run()
finally:
    sys.modules.pop("go", None)
