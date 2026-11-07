"""Windows-native environment sensors used by Eris Deep Environment.

Every module is optional and fails closed on unsupported platforms.  Runtime
code can therefore import the package in Linux CI without pretending that a
Windows API exists there.
"""

from .process_resolver import ProcessIdentity, ProcessResolver

__all__ = ["ProcessIdentity", "ProcessResolver"]
