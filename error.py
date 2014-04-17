# Copyright 2014 Miguel Martinez de Aguirre
# See LICENSE for more details

from twisted.cred import error
from twisted.spread import pb


class NameTaken(error.LoginFailed):
    """
    Names are unique. This exception is thrown when a user requests
    a name which is already taken.
    """
    pass


class VersionMismatch(error.LoginFailed):
    """Client version is not recognised by this server."""
    pass


class IllegalNodeExpansion(pb.Error):
    """Attempted node expansion not allowed."""
    pass
