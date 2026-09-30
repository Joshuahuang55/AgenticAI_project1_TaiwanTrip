"""HTTP sessions for Taiwan government sites whose certificates fail Python 3.13+'s strict checks.

Several .gov.tw certificates (CWA, DGPA) lack a Subject Key Identifier, which Python 3.13+
rejects under VERIFY_X509_STRICT. Keep full chain and hostname checks; drop only that strict profile.
"""

import ssl

import requests
from requests.adapters import HTTPAdapter


class GovTLS(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def gov_session(*url_prefixes: str) -> requests.Session:
    """A Session that uses the relaxed profile only for the given URL prefixes."""
    session = requests.Session()
    for prefix in url_prefixes:
        session.mount(prefix, GovTLS())
    return session
