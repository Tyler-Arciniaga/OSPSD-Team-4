from collections import OrderedDict
from threading import Lock
from time import monotonic

from app.errors import ErrorCode, ServiceError
from app.interface import IdempotentIssueProvider, IssueProvider
from app.models import Issue, IssueUpdate


class CachedIssueProvider:
    """Cache successful reads, coordinating reads and writes by issue ID."""

    def __init__(
        self,
        provider: IssueProvider,
        ttl_seconds: float = 30,
        max_entries: int = 100,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self._provider = provider
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries

        # Entries run from least to most recently used.
        self._entries: OrderedDict[str, tuple[float, Issue]] = OrderedDict()
        self._entries_lock = Lock()

        # A fixed pool bounds lock storage. The same ID always selects the
        # same lock; different IDs may collide and also have to take turns.
        self._stripes = [Lock() for _ in range(64)]

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        """Return a valid cached copy, or fetch and save a fresh issue."""
        # Always acquire the stripe before the dictionary lock. Keep the
        # stripe during retrieval so a write cannot race with saving a read.
        with self._stripes[hash(issue_id) % len(self._stripes)]:
            with self._entries_lock:
                # A fresh read must not fall back to stale data if retrieval fails.
                if fresh:
                    self._entries.pop(issue_id, None)
                cached = self._entries.get(issue_id)
                if cached is not None:
                    expires_at, issue = cached
                    if monotonic() < expires_at:
                        self._entries.move_to_end(issue_id)
                        return issue.model_copy(deep=True)
                    # Check only this entry; never scan the entire cache.
                    del self._entries[issue_id]

            # Release the dictionary lock while waiting for the provider.
            # Requests on other stripes can still use the cache.
            # Failures propagate normally and are never cached.
            issue = (
                self._provider.get_issue(issue_id, fresh=True)
                if fresh
                else self._provider.get_issue(issue_id)
            )
            expires_at = monotonic() + self._ttl_seconds

            with self._entries_lock:
                self._entries[issue_id] = (
                    expires_at,
                    issue.model_copy(deep=True),
                )
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return issue

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Discard old data before attempting the update, even if it fails."""
        with self._stripes[hash(issue_id) % len(self._stripes)]:
            with self._entries_lock:
                self._entries.pop(issue_id, None)
            # Keep this issue's stripe locked until the write finishes.
            return self._provider.update_issue(issue_id, changes)

    def delete_issue(self, issue_id: str) -> None:
        """Discard old data before attempting deletion, even if it fails."""
        with self._stripes[hash(issue_id) % len(self._stripes)]:
            with self._entries_lock:
                self._entries.pop(issue_id, None)
            self._provider.delete_issue(issue_id)

    def create_issue(self, title: str) -> Issue:
        """Forward creation without reading or changing the cache."""
        return self._provider.create_issue(title)

    def create_tagged_issue(self, title: str, creation_token: str) -> Issue:
        """Forward tagged creation without reading or changing the cache."""
        return self._idempotent_provider().create_tagged_issue(title, creation_token)

    def find_tagged_issue(self, creation_token: str) -> Issue | None:
        """Forward the lookup uncached: recovery must see the provider's state."""
        return self._idempotent_provider().find_tagged_issue(creation_token)

    def supports_idempotent_creation(self) -> bool:
        return isinstance(self._provider, IdempotentIssueProvider)

    def _idempotent_provider(self) -> IdempotentIssueProvider:
        if not isinstance(self._provider, IdempotentIssueProvider):
            # Raised before any request, so nothing was written.
            raise ServiceError(
                ErrorCode.SERVICE_NOT_CONFIGURED,
                "Idempotent creation is not available",
                cause="provider_not_idempotent",
            )
        return self._provider
