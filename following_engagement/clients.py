from __future__ import annotations

import json
import time
from typing import Any

import requests
from requests_oauthlib import OAuth1

from .models import ActionType, Analysis, TimelinePost


class XTimelineClient:
    BASE_URL = "https://api.x.com/2"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        # X_ACCESS_TOKEN is the existing OAuth 1.0a user token, not a bearer token.
        self.session.auth = OAuth1(api_key, api_secret, access_token, access_token_secret)

    def fetch(self, user_id: str, since_id: str | None, maximum: int = 300) -> list[TimelinePost]:
        posts: list[TimelinePost] = []
        token = None
        while len(posts) < maximum:
            params = {
                "max_results": min(100, maximum - len(posts)),
                "exclude": "retweets,replies",
                "tweet.fields": "created_at,author_id,conversation_id,public_metrics,entities,referenced_tweets",
                "expansions": "author_id",
                "user.fields": "username,name,verified,public_metrics",
            }
            if since_id:
                params["since_id"] = since_id
            if token:
                params["pagination_token"] = token
            payload = self._get(
                f"{self.BASE_URL}/users/{user_id}/timelines/reverse_chronological", params
            )
            users = {
                u["id"]: u.get("username", "") for u in payload.get("includes", {}).get("users", [])
            }
            for item in payload.get("data", []):
                refs = item.get("referenced_tweets", [])
                posts.append(
                    TimelinePost(
                        post_id=str(item["id"]),
                        author_id=str(item.get("author_id", "")),
                        author_username=users.get(str(item.get("author_id", "")), ""),
                        text=item.get("text", ""),
                        metrics=item.get("public_metrics", {}),
                        created_at=item.get("created_at"),
                        is_reply=any(r.get("type") == "replied_to" for r in refs),
                        is_repost=any(r.get("type") == "retweeted" for r in refs),
                    )
                )
            token = payload.get("meta", {}).get("next_token")
            if not token or not payload.get("data"):
                break
        return posts[:maximum]

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code < 400:
                return response.json()
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
        raise RuntimeError("unreachable")


class OpenAiAnalyzer:
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str, session: requests.Session | None = None) -> None:
        self.api_key, self.model = api_key, model
        self.session = session or requests.Session()

    def analyze(self, post: TimelinePost) -> Analysis:
        schema_prompt = (
            "Analyze this X post for investment engagement. Return only JSON with keys: relevant, "
            "category, relevanceScore, importanceScore, engagementValue, contentValue, summary, "
            "recommendedAction (SKIP|QUOTE|POST|PERMITTED_REPLY|REVIEW_ONLY), reason, generatedText. "
            "All scores must be integers 0..100. Do not invent facts. Input: "
            + json.dumps(
                {
                    "postId": post.post_id,
                    "author": post.author_username,
                    "text": post.text,
                    "metrics": post.metrics,
                },
                ensure_ascii=False,
            )
        )
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": schema_prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=45,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(str(response.status_code))
                response.raise_for_status()
                return self.parse(response.json()["choices"][0]["message"]["content"])
            except (ValueError, KeyError, requests.RequestException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise ValueError("OpenAI analysis failed") from last_error

    @staticmethod
    def parse(content: str) -> Analysis:
        data = json.loads(content)

        def score(key: str) -> int:
            return max(0, min(100, int(data[key])))

        return Analysis(
            bool(data["relevant"]),
            str(data["category"]),
            score("relevanceScore"),
            score("importanceScore"),
            score("engagementValue"),
            score("contentValue"),
            str(data["summary"]),
            ActionType(str(data["recommendedAction"]).upper()),
            str(data["reason"]),
            str(data.get("generatedText", "")),
        )


class XWriteClient:
    """LIVE-only adapter. It is never injected into simulation executors."""

    def __init__(self) -> None:
        import os

        import tweepy

        self.client = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"],
            consumer_secret=os.environ["X_API_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"],
            access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
        )

    def write(self, action: ActionType, text: str, post_id: str) -> str:
        kwargs = {"quote_tweet_id": post_id} if action == ActionType.QUOTE else {}
        response = self.client.create_tweet(text=text, **kwargs)
        return str(response.data["id"])
