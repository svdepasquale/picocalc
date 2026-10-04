# Opens the toolkit menu at boot, after the firmware's boot.py has set up
# screen and keyboard. q in the menu leaves to the REPL; delete this file
# to boot into a plain REPL.
try:
    import menu

    menu.run()
except Exception as error:
    import sys

    sys.print_exception(error)
