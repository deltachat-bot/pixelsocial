"""Bot CLI"""

from deltabot_cli import BotCli

from ._version import __version__

cli = BotCli("pixelsocial")
cli.add_generic_option("-v", "--version", action="version", version=__version__)
cli.add_generic_option(
    "--interval",
    type=int,
    default=60 * 5,
    help="how many seconds to sleep before checking the feeds again (default: %(default)s)",
)
cli.add_generic_option(
    "--parallel",
    type=int,
    default=10,
    help="how many feeds to check in parallel (default: %(default)s)",
)
cli.add_generic_option(
    "--no-time",
    help="do not display date timestamp in log messages",
    action="store_false",
)
