"""Install and migrate entry point.

Everything this app configures on a site is applied from here, so a fresh
install and every ``bench migrate`` converge on the same declared state.
Each step must be idempotent.
"""

from meddevice_suite.setup.desk import apply_desk_configuration


def apply_configuration() -> None:
	apply_desk_configuration()
