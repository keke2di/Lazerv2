"""Start the app from a checkout.

    python main.py           open the window
    python main.py daemon    run the background Conversation Guard

Installing the package gives you `lazerv2` and `lazerv2-guard` instead.
"""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "daemon":
        from lazerv2.tray_daemon import main as daemon_main
        sys.argv = [sys.argv[0], *argv[1:]]
        return daemon_main()

    from lazerv2.app import main as app_main
    sys.argv = [sys.argv[0], *argv]
    return app_main()


if __name__ == "__main__":
    sys.exit(main())