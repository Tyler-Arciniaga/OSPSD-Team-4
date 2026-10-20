from collections import OrderedDict
from threading import Lock
from time import monotonic

from app.interface import IssueProvider
from app.models import Issue, IssueUpdate


class CachedIssueProvider:
    """Cache successful issue reads around an existing provider.

    Saved results expire after a fixed lifetime. Updates and deletions
    remove the affected entry before contacting the underlying provider.
    """

    def __init__(
        self,
        provider: IssueProvider,
        ttl_seconds: float = 30,
        max_entries: int = 100,
    ) -> None:
        # Require a positive lifetime and storage limit.
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        # The underlying provider retrieves and changes issues.
        self._provider = provider
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries

        # Each issue ID maps to (expiration time, saved Issue).
        # We maintain the order from least to most recently used.
        self._entries: OrderedDict[str, tuple[float, Issue]] = OrderedDict()

        # Reads, updates, and deletions share this same lock.
        # It stays held during provider calls, so these operations take turns even when they concern different issue IDs.
        self._lock = Lock()

    def get_issue(self, issue_id: str) -> Issue:
        """Return a valid cached copy, or fetch and save a fresh issue."""
        with self._lock:
            # Clear expired entries before looking for this issue.
            self._remove_expired(monotonic())
            cached = self._entries.get(issue_id)

            if cached is not None:
                expires_at, issue = cached

                # Check again in case it expired after cleanup.
                if monotonic() < expires_at:
                    # Mark it as most recently used without extending its expiration time.
                    self._entries.move_to_end(issue_id)

                    # A separate copy protects our saved object from changes made by the caller.
                    return issue.model_copy(deep=True)

                del self._entries[issue_id]

            # Cache miss: retrieve the issue from the provider.
            # If this raises an error, nothing is saved.
            issue = self._provider.get_issue(issue_id)

            # Start the lifetime after retrieval finishes.
            expires_at = monotonic() + self._ttl_seconds
            self._entries[issue_id] = (
                expires_at,
                issue.model_copy(deep=True),
            )

            # Remove the least recently used entries if over capacity.
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

            return issue

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Remove the saved issue and forward the update."""
        with self._lock:
            # Clear the old cached value first.
            # None prevents an error if this ID is not cached.
            self._entries.pop(issue_id, None)

            # The next GET will retrieve and cache the updated issue.
            return self._provider.update_issue(issue_id, changes)

    def delete_issue(self, issue_id: str) -> None:
        """Remove the saved issue and forward the deletion."""
        with self._lock:
            # Discard the cached result even if deletion later fails.
            self._entries.pop(issue_id, None)
            self._provider.delete_issue(issue_id)

    def create_issue(self, title: str) -> Issue:
        """Forward creation without reading or changing the cache."""
        # The new issue's first GET will retrieve and cache it.
        return self._provider.create_issue(title)

    def _remove_expired(self, now: float) -> None:
        """Remove expired entries while the caller holds the lock."""
        # Collect IDs first: we cannot change the dictionary's size
        # while iterating over it. "_" ignores the saved Issue object.
        expired_ids = [
            issue_id
            for issue_id, (expires_at, _) in self._entries.items()
            if now >= expires_at
        ]

        for issue_id in expired_ids:
            del self._entries[issue_id]
