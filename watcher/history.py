import base64
from dataclasses import dataclass, replace
from datetime import datetime
import json
import re
from urllib.parse import urlencode

from .http import RequestFailed
from .yahoo import AUCTION_ID, timestamp


@dataclass(frozen=True)
class History:
    initialized: bool = False
    skip_before: datetime | None = None
    sent: frozenset[str] = frozenset()

    @classmethod
    def read(cls, data: bytes):
        try:
            raw = json.loads(data)
            ids = raw["sent"]
            initialized = raw["initialized"]
            cutoff = raw["skip_before"]
            if (raw["version"] != 1 or type(initialized) is not bool or not isinstance(ids, list)
                    or any(not isinstance(id_, str) or not AUCTION_ID.fullmatch(id_) for id_ in ids)
                    or (not initialized and (cutoff is not None or ids))):
                raise ValueError
            return cls(initialized, None if cutoff is None else timestamp(cutoff), frozenset(ids))
        except (ValueError, TypeError, KeyError):
            raise ValueError("通知履歴の形式が不正です") from None

    def encode(self) -> bytes:
        data = {
            "version": 1, "initialized": self.initialized,
            "skip_before": self.skip_before.isoformat() if self.skip_before else None,
            "sent": sorted(self.sent),
        }
        return (json.dumps(data, indent=2) + "\n").encode("utf-8")

    def excludes(self, id_: str, started: datetime) -> bool:
        return id_ in self.sent or (self.skip_before is not None and started <= self.skip_before)

    def record(self, id_: str):
        return replace(self, sent=self.sent | {id_})


class LocalHistory:
    def __init__(self, path):
        self.path = path

    def load(self) -> History:
        try:
            return History.read(self.path.read_bytes())
        except FileNotFoundError:
            return History()

    def save(self, history: History):
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_bytes(history.encode())
        temp.replace(self.path)


class GitHubHistory:
    def __init__(self, http, repository: str, branch: str, token: str):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not branch or not token:
            raise ValueError("GitHub のリポジトリ、ブランチ、トークンを確認してください")
        self.http = http
        self.branch = branch
        self.url = f"https://api.github.com/repos/{repository}/contents/state/history.json"
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}
        self.sha: str | None = None

    def load(self) -> History:
        url = f"{self.url}?{urlencode({'ref': self.branch})}"
        raw = self.http.request("GitHub 履歴読込", url, headers=self.headers, retry=True)
        try:
            data = json.loads(raw)
            if (data["type"] != "file" or data["encoding"] != "base64"
                    or re.fullmatch(r"[0-9a-f]{40}", data["sha"]) is None):
                raise ValueError
            history = History.read(base64.b64decode("".join(data["content"].split()), validate=True))
            self.sha = data["sha"]
            return history
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("GitHub の履歴ファイルを読み取れません") from None

    def save(self, history: History):
        if self.sha is None:
            raise RuntimeError("履歴の保存前に既存ファイルを読み込んでください")
        payload = {
            "message": "chore: update tire notification history",
            "branch": self.branch, "sha": self.sha,
            "content": base64.b64encode(history.encode()).decode("ascii"),
        }
        try:
            raw = self.http.request("GitHub 履歴保存", self.url, method="PUT", payload=payload, headers=self.headers)
            sha = json.loads(raw)["content"]["sha"]
            if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
                raise ValueError
            self.sha = sha
        except (RequestFailed, ValueError, KeyError, TypeError):
            # A lost response can happen after a successful write. Read back
            # before retrying a notification or touching a conflicting file.
            if self.load() != history:
                raise RuntimeError("履歴を保存できません。権限・ブランチ保護・競合を確認してください") from None
