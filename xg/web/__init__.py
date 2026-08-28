"""Safe, read-only web capabilities for XG."""

from xg.web.fetch import WebFetchService
from xg.web.models import (
    FetchRequest,
    FetchResponse,
    ProviderHealth,
    SearchRequest,
    SearchResponse,
    SearchResult,
    WebFetchConfig,
    WebSearchConfig,
    WebConfig,
)
from xg.web.search import WebSearchService

__all__ = [
    "FetchRequest", "FetchResponse", "ProviderHealth", "SearchRequest",
    "SearchResponse", "SearchResult", "WebFetchConfig", "WebSearchConfig",
    "WebConfig", "WebFetchService", "WebSearchService",
]
