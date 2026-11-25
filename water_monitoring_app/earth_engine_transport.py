"""Bounded requests-based HTTP transport for the Earth Engine client."""
import httplib2
import requests
from http import HTTPStatus

from .earth_engine_network import EarthEngineSession


class EarthEngineTransport:
    """Expose the httplib2 request interface using a requests session."""

    def __init__(self, timeout, session=None):
        self.timeout = timeout
        self.session = session if session is not None else EarthEngineSession()

    def request(self, uri, method='GET', body=None, headers=None,
                redirections=None, connection_type=None):
        try:
            response = self.session.request(
                method, uri, data=body, headers=headers, timeout=self.timeout
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as error:
            raise ConnectionError(error) from error
        except requests.exceptions.Timeout as error:
            raise TimeoutError(error) from error
        response_headers = dict(response.headers)
        response_headers['status'] = response.status_code
        result = httplib2.Response(response_headers)
        # httplib2.Response(dict) otherwise retains its class default "Ok",
        # even for a 403. Preserve the actual HTTP reason for googleapiclient.
        reason = getattr(response, 'reason', None)
        if not isinstance(reason, str) or not reason:
            try:
                reason = HTTPStatus(response.status_code).phrase
            except ValueError:
                reason = 'HTTP response'
        result.reason = reason
        return result, response.content
