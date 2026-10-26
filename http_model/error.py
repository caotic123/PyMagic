# the model of httplib2.error: a copy of each exception, which stands in for it in a run
# (copied from httplib2 0.32, MIT license, Copyright 2006 Joe Gregorio)

import httplib2.error

from PyMagic import model


@model(httplib2.error.HttpLib2Error)
class HttpLib2Error(Exception):
    # all exceptions raised here derive from it
    pass

@model(httplib2.error.HttpLib2ErrorWithResponse)
class HttpLib2ErrorWithResponse(HttpLib2Error):
    # one that can be caught and turned back into a response
    def __init__(self, desc, response, content):
        self.response = response
        self.content = content
        HttpLib2Error.__init__(self, desc)

@model(httplib2.error.RedirectMissingLocation)
class RedirectMissingLocation(HttpLib2ErrorWithResponse):
    pass

@model(httplib2.error.RedirectLimit)
class RedirectLimit(HttpLib2ErrorWithResponse):
    pass

@model(httplib2.error.FailedToDecompressContent)
class FailedToDecompressContent(HttpLib2ErrorWithResponse):
    pass

@model(httplib2.error.UnimplementedDigestAuthOptionError)
class UnimplementedDigestAuthOptionError(HttpLib2ErrorWithResponse):
    pass

@model(httplib2.error.UnimplementedHmacDigestAuthOptionError)
class UnimplementedHmacDigestAuthOptionError(HttpLib2ErrorWithResponse):
    pass

@model(httplib2.error.MalformedHeader)
class MalformedHeader(HttpLib2Error):
    pass

@model(httplib2.error.RelativeURIError)
class RelativeURIError(HttpLib2Error):
    pass

@model(httplib2.error.ServerNotFoundError)
class ServerNotFoundError(HttpLib2Error):
    pass

@model(httplib2.error.ProxiesUnavailableError)
class ProxiesUnavailableError(HttpLib2Error):
    pass


@model(httplib2.error)
class _model_error:
    # the copies of its exceptions
    HttpLib2Error = HttpLib2Error
    HttpLib2ErrorWithResponse = HttpLib2ErrorWithResponse
    RedirectMissingLocation = RedirectMissingLocation
    RedirectLimit = RedirectLimit
    FailedToDecompressContent = FailedToDecompressContent
    UnimplementedDigestAuthOptionError = UnimplementedDigestAuthOptionError
    UnimplementedHmacDigestAuthOptionError = UnimplementedHmacDigestAuthOptionError
    MalformedHeader = MalformedHeader
    RelativeURIError = RelativeURIError
    ServerNotFoundError = ServerNotFoundError
    ProxiesUnavailableError = ProxiesUnavailableError
