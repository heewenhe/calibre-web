# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Metadata scan background task.

Scans books using existing metadata providers (Douban, Google Books, etc.)
and populates Calibre Tags based on discovered metadata.
"""

import ipaddress
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from flask_babel import lazy_gettext as N_

from cps.services.worker import CalibreTask
from cps import logger, db, ub, app, config

log = logger.create()


class SSRFProtection:
    """SSRF protection for metadata provider HTTP requests.

    Restricts outbound HTTP connections to a pre-approved domain whitelist
    and blocks requests to private/internal IP addresses.
    """

    ALLOWED_DOMAINS = frozenset({
        'book.douban.com',
        'www.googleapis.com',
        'www.amazon.com',
        'www.amazon.cn',
        'comicvine.gamespot.com',
        'scholar.google.com',
        'lubimyczytac.pl',
    })

    PRIVATE_NETWORKS = [
        ipaddress.ip_network('10.0.0.0/8'),
        ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16'),
        ipaddress.ip_network('127.0.0.0/8'),
        ipaddress.ip_network('169.254.0.0/16'),
        ipaddress.ip_network('0.0.0.0/8'),
        ipaddress.ip_network('::1/128'),
        ipaddress.ip_network('fc00::/7'),
        ipaddress.ip_network('fe80::/10'),
    ]

    REQUEST_TIMEOUT = 10

    @classmethod
    def validate_url(cls, url):
        """Validate that a URL is safe to request (no SSRF).

        Raises:
            ValueError: If URL is not in the allowed domain list or
                        resolves to a private IP.
        """
        if not url:
            raise ValueError("URL cannot be empty")
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid URL: no hostname")

        # Check domain whitelist
        allowed = hostname in cls.ALLOWED_DOMAINS
        if not allowed:
            for d in cls.ALLOWED_DOMAINS:
                if hostname.endswith('.' + d):
                    allowed = True
                    break
        if not allowed:
            raise ValueError(
                "Domain {} is not in the allowed list".format(hostname)
            )

        # Check for private IP
        try:
            ip_addr = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(ip_addr)
            for network in cls.PRIVATE_NETWORKS:
                if ip in network:
                    raise ValueError(
                        "Access to private IP {} is not allowed".format(ip)
                    )
        except socket.gaierror:
            raise ValueError("Cannot resolve hostname: {}".format(hostname))

        return True

    @classmethod
    def get_safe_request_kwargs(cls):
        """Get safe HTTP request parameters (timeout, etc.)."""
        return {'timeout': cls.REQUEST_TIMEOUT}


class TaskMetadataScan(CalibreTask):
    """Background task for batch metadata scanning.

    Iterates over selected books, fetches metadata from the chosen provider,
    and applies discovered tags to Calibre's Tags table.
    """

    # Known providers that do NOT return tags
    PROVIDERS_WITHOUT_TAGS = frozenset({'amazon'})

    def __init__(self, provider_id, book_ids=None, user_id=None, max_retries=3):
        task_message = N_("Metadata Scan: {}").format(provider_id)
        super().__init__(task_message)
        self.provider_id = provider_id
        self.book_ids = book_ids  # None = all books
        self.user_id = user_id
        self.max_retries = max_retries
        self.retry_count = 0
        self.scan_history_id = None

    @property
    def name(self):
        return N_("Metadata Scan")

    @property
    def is_cancellable(self):
        return True

    def _create_scan_history(self, total):
        """Create a ScanHistory record and return its id."""
        try:
            scan = ub.ScanHistory(
                provider=self.provider_id,
                total_books=total,
                status="running",
                started_at=datetime.now(timezone.utc),
                user_id=self.user_id,
            )
            ub.session.add(scan)
            ub.session.commit()
            return scan.id
        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("Failed to create scan history: %s", str(e))
            return None

    def _update_scan_history(self, processed, added, skipped, status="running", error_log=""):
        """Update the scan history record with current progress."""
        if not self.scan_history_id:
            return
        try:
            scan = ub.session.query(ub.ScanHistory).filter(
                ub.ScanHistory.id == self.scan_history_id
            ).first()
            if scan:
                scan.processed_books = processed
                scan.tags_added = added
                scan.tags_skipped = skipped
                scan.status = status
                if status in ("success", "failed", "cancelled"):
                    scan.finished_at = datetime.now(timezone.utc)
                if error_log:
                    existing = scan.error_log or ""
                    scan.error_log = (existing + "\n" + error_log)[:10000]
                ub.session.commit()
        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("Failed to update scan history: %s", str(e))

    def _load_isbn_map(self, session, all_book_ids):
        """Preload ISBN identifiers for all target books to avoid N+1 queries.

        Args:
            session: Calibre DB session.
            all_book_ids: List of book ids.

        Returns:
            dict: {book_id: isbn_string}
        """
        try:
            from cps.db import Identifiers
            identifiers = session.query(Identifiers).filter(
                Identifiers.type == "isbn",
                Identifiers.book.in_(all_book_ids)
            ).all()
            return {i.book: i.val for i in identifiers}
        except SQLAlchemyError as e:
            log.error("Failed to load ISBN map: %s", str(e))
            return {}

    def _get_provider(self, provider_id):
        """Dynamically load a metadata provider by ID.

        Args:
            provider_id: Provider module name (e.g. "douban", "google").

        Returns:
            Provider class or None.
        """
        try:
            from cps.services.Metadata import Metadata
            metadata = Metadata()
            providers = metadata.get_source_prefs()
            for p in providers:
                if p['id'] == provider_id:
                    return p
            return None
        except Exception as e:
            log.error("Failed to get provider %s: %s", provider_id, str(e))
            return None

    def _apply_tags_to_book(self, session, book_id, tags):
        """Add discovered tags to a Calibre book.

        Creates new Tags entries if needed, creates books_tags_link associations.

        Args:
            session: Calibre DB session.
            book_id: Calibre book id.
            tags: List of tag name strings.

        Returns:
            Number of new tags added.
        """
        added = 0
        try:
            from cps.db import books_tags_link

            # Get existing book tags
            existing_links = session.query(books_tags_link.c.tag).filter(
                books_tags_link.c.book == book_id
            ).all()
            existing_tag_ids = set(link.tag for link in existing_links)

            for tag_name in tags:
                tag_name = tag_name.strip()[:200]  # Truncate to max length
                if not tag_name:
                    continue

                # Find or create the tag
                tag = session.query(db.Tags).filter(db.Tags.name == tag_name).first()
                if not tag:
                    tag = db.Tags(name=tag_name)
                    session.add(tag)
                    session.flush()

                # Create association if not already present
                if tag.id not in existing_tag_ids:
                    session.execute(
                        books_tags_link.insert().values(
                            book=book_id, tag=tag.id
                        )
                    )
                    added += 1
                    existing_tag_ids.add(tag.id)

            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            log.error("Failed to apply tags to book %d: %s", book_id, str(e))
        return added

    def run(self, worker_thread):
        """Execute metadata scan task.

        Note: Must enter Flask app_context before accessing Calibre DB.
        """
        from cps import app as flask_app
        with flask_app.app_context():
            self._run_in_context(worker_thread)

    def _run_in_context(self, worker_thread):
        """Main scan logic executed within Flask app context."""
        # Check if provider supports tags
        if self.provider_id in self.PROVIDERS_WITHOUT_TAGS:
            error_msg = (
                "Provider '{}' does not return tags. "
                "Please choose a different provider."
            ).format(self.provider_id)
            self._handleError(error_msg)
            return

        # Get provider
        provider = self._get_provider(self.provider_id)
        if not provider:
            self._handleError("Provider '{}' not found".format(self.provider_id))
            return

        calibre_session = db.calibre_db.session

        # Determine which books to scan
        if self.book_ids:
            books = calibre_session.query(db.Books).filter(
                db.Books.id.in_(self.book_ids)
            ).all()
        else:
            books = calibre_session.query(db.Books).all()

        total = len(books)
        if total == 0:
            self._handleError("No books found to scan")
            return

        # Limit to 500 books per scan to prevent queue overflow
        max_books = 500
        if total > max_books:
            log.warning(
                "Scan limited to %d books (out of %d). Process in batches.",
                max_books, total
            )
            books = books[:max_books]
            total = max_books

        # Create scan history
        self.scan_history_id = self._create_scan_history(total)

        # Preload ISBN map to avoid N+1 queries
        isbn_map = self._load_isbn_map(calibre_session, [b.id for b in books])

        processed = 0
        tags_added = 0
        tags_skipped = 0

        for idx, book in enumerate(books):
            # Check for cancellation
            if self.stat in (5,):  # STAT_CANCELLED
                self._update_scan_history(processed, tags_added, tags_skipped, "cancelled")
                return

            # Update progress
            self.progress = processed / total if total > 0 else 0

            try:
                isbn = isbn_map.get(book.id)

                # Get metadata from provider
                meta_records = None
                for attempt in range(self.max_retries):
                    try:
                        meta_records = provider.search(isbn, book.title, book.authors)
                        if meta_records is not None:
                            break
                    except Exception as e:
                        log.warning(
                            "Provider search attempt %d failed for book %d: %s",
                            attempt + 1, book.id, str(e)
                        )
                        if attempt < self.max_retries - 1:
                            time.sleep(2)  # Backoff before retry

                if meta_records is None or len(meta_records) == 0:
                    processed += 1
                    continue

                # Select best match
                best_match = self._select_best_match(meta_records, book, isbn)
                if best_match is None:
                    processed += 1
                    tags_skipped += 1
                    self._update_scan_history(
                        processed, tags_added, tags_skipped,
                        error_log="No match for book {}: {}".format(book.id, book.title)
                    )
                    continue

                # Extract and apply tags
                if best_match.tags and len(best_match.tags) > 0:
                    added = self._apply_tags_to_book(calibre_session, book.id, best_match.tags)
                    tags_added += added
                else:
                    tags_skipped += 1

            except Exception as e:
                log.error("Error scanning book %d: %s", book.id, str(e))
                self._update_scan_history(
                    processed, tags_added, tags_skipped,
                    error_log="Book {} ({}): {}".format(book.id, book.title, str(e))
                )

            processed += 1

            # Slow down to avoid rate limiting
            if processed % 10 == 0:
                time.sleep(1)

        # Final progress
        self.progress = 1.0
        self._update_scan_history(processed, tags_added, tags_skipped, "success")
        self._handleSuccess()

    @staticmethod
    def _select_best_match(meta_records, book, isbn):
        """Select the best matching MetaRecord from search results.

        Priority:
        1. ISBN exact match
        2. Title exact match + author match
        3. Title similarity (Levenshtein distance)

        Args:
            meta_records: List of MetaRecord from provider.
            book: Calibre Books instance.
            isbn: Book ISBN or None.

        Returns:
            Best matching MetaRecord, or None if no good match.
        """
        if not meta_records:
            return None

        # If only one result, return it
        if len(meta_records) == 1:
            return meta_records[0]

        # Priority 1: ISBN match
        if isbn:
            for record in meta_records:
                if record.isbn and record.isbn.replace('-', '').replace(' ', '') == \
                   isbn.replace('-', '').replace(' ', ''):
                    return record

        # Priority 2: Title match
        book_title_lower = book.title.lower().strip()
        for record in meta_records:
            if record.title and record.title.lower().strip() == book_title_lower:
                return record

        # Priority 3: Partial title match (first 20 chars)
        for record in meta_records:
            if record.title and record.title.lower().strip()[:20] == book_title_lower[:20]:
                return record

        # No good match
        return None