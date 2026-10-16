"""Accept trusted local method requests; expose versioned postprocessing services.

Registration and discovery never execute bundles. Execution is explicit and uses
the configured interpreter with user permissions, without an isolation claim.
"""

__version__ = "0.1.0"
