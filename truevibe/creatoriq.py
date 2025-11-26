from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import requests


CREATORIQ_GRAPHQL_URL = "https://app.creatoriq.com/api/collections/graphql"


GET_COLLECTION_CREATORS_QUERY = """
query getCollectionCreators {
  lists {
    edges {
      node {
        id
        items {
          creator {
            id
            listCreatorsId
            fullName
            primaryNetwork
            primarySocialUsername
            profilePictureURL
            source
            totalSocialConnections
            age
            country
            city
            gender
            language
            tags
            categories
            subCategories
            sections
            attributes {
              description
            }
            accounts {
              id
              network
              socialNetworkId
              socialUsername
              followers
              engagementRate
              accountUrl
            }
          }
        }
      }
    }
  }
}
"""


GET_COLLECTION_CREATOR_DETAILS_QUERY = """
fragment CreatorAttributes on CustomAttributes {
  networkPublisherId
  publisherSize
  subNetworkName
  accountManagerName
  description
  shortDescription
  contentRating
  relationship
}

fragment CreatorProfileSettings on ProfileSettings {
  isHidden
  config {
    field
    isHidden
  }
}

query getCollectionCreatorDetails($filterBy: ListDataFilter, $creatorId: ID!) {
  lists(filterBy: $filterBy) {
    edges {
      node {
        id
        items {
          creator {
            id
            listCreatorsId
            name
            username
            handle
            platform
            primaryPlatform
            sections
            profileUrl
            profileImage
            customFields {
              id
              value
            }
            attributes {
              ...CreatorAttributes
            }
            profileSettings {
              ...CreatorProfileSettings
            }
            audience {
              countries {
                title
                value
              }
              age {
                title
                value
              }
              gender {
                title
                value
              }
              interests {
                title
                value
              }
            }
            statistics {
              followers
              engagementRate
              views
            }
          }
        }
      }
    }
  }
}
"""


class CreatorIQError(RuntimeError):
    """Raised when CreatorIQ API returns an error."""


def extract_slug(publish_link: str) -> str:
    match = re.search(r"/lists/report/([^/?#]+)", publish_link)
    if not match:
        raise ValueError("Unable to extract CreatorIQ share slug from the provided URL.")
    return match.group(1)


def is_creatoriq_link(publish_link: str) -> bool:
    return "creatoriq.com" in publish_link


@dataclass
class CreatorRecord:
    data: Dict[str, object]
    detail: Optional[Dict[str, object]] = None

    def merged(self) -> Dict[str, object]:
        payload: Dict[str, object] = {}
        for source in (self.data or {}, self.detail or {}):
            for key, value in source.items():
                if value is None:
                    continue
                payload[key] = value
        return payload


class CreatorIQClient:
    """
    Lightweight GraphQL client for the CreatorIQ share endpoint.

    CreatorIQ uses Apollo GraphQL under the hood. The shared report slug is
    included in the `Authorization` header using the `Report <slug>` format.
    """

    def __init__(self, slug: str, session: Optional[requests.Session] = None, timeout: int = 20) -> None:
        self.slug = slug
        self.session = session or requests.Session()
        self.timeout = timeout
        self._list_id: Optional[str] = None

    def _graphql(self, operation_name: str, query: str, variables: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        payload = {
            "operationName": operation_name,
            "variables": variables or {},
            "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}},
            "query": query,
        }
        headers = {
            "Authorization": f"Report {self.slug}",
            "Content-Type": "application/json",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Origin": "https://vero.creatoriq.com",
            "Referer": "https://vero.creatoriq.com/",
        }
        response = self.session.post(
            CREATORIQ_GRAPHQL_URL,
            data=json.dumps(payload),
            headers=headers,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise CreatorIQError(f"CreatorIQ request failed with status {response.status_code}: {response.text}")
        data = response.json()
        if "errors" in data:
            raise CreatorIQError(f"CreatorIQ query error: {data['errors']}")
        return data.get("data") or {}

    def fetch_creators(self) -> List[Dict[str, object]]:
        data = self._graphql("getCollectionCreators", GET_COLLECTION_CREATORS_QUERY)
        lists = ((data.get("lists") or {}).get("edges")) or []
        if not lists:
            return []
        node = lists[0].get("node") or {}
        list_id = node.get("id")
        if list_id:
            self._list_id = str(list_id)
        items: Iterable[Dict[str, object]] = node.get("items") or []
        creators: List[Dict[str, object]] = []
        for item in items:
            creator = item.get("creator") if item else None
            if creator:
                creators.append(creator)
        return creators

    def fetch_creator_detail(self, creator_id: str) -> Optional[Dict[str, object]]:
        list_id = self._list_id
        try:
            data = self._graphql(
                "getCollectionCreatorDetails",
                GET_COLLECTION_CREATOR_DETAILS_QUERY,
                {"creatorId": creator_id, "filterBy": {"id": {"eq": list_id}} if list_id else None},
            )
        except CreatorIQError:
            return None
        lists = ((data.get("lists") or {}).get("edges")) or []
        if not lists:
            return None
        node = lists[0].get("node") or {}
        items = node.get("items") or []
        for item in items:
            creator = item.get("creator")
            if creator and str(creator.get("id")) == str(creator_id):
                return creator
        return None
