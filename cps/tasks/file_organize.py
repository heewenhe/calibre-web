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
File organization background task.

Applies file organization rules by creating symlinks/hardlinks in
tag-based directory structures without modifying Calibre's original layout.
"""

from flask_babel import lazy_gettext as N_

from cps.services.worker import CalibreTask
from cps import logger

log = logger.create()


class TaskFileOrganize(CalibreTask):
    """Background task for batch file organization.

    Applies file organization rules to selected books, creating symlinks
    or hardlinks in target directories based on tag matching rules.
    """

    def __init__(self, rule_ids=None, book_ids=None, user_id=None, max_retries=3):
        super().__init__(N_("File Organization"))
        self.rule_ids = rule_ids  # Specific rules to apply, None = all
        self.book_ids = book_ids  # Specific books, None = all
        self.user_id = user_id
        self.max_retries = max_retries
        self.retry_count = 0
        self._processed = 0
        self._total = 0

    @property
    def name(self):
        return N_("File Organization")

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        """Execute file organization task.

        Note: Must enter Flask app_context before accessing database.
        Processes books in batches of 50, supports progress reporting
        and failure retry.
        """
        from cps import app as flask_app
        with flask_app.app_context():
            self._run_in_context(worker_thread)

    def _run_in_context(self, worker_thread):
        """Main file organization logic within Flask app context."""
        from cps.services.FileOrganizer import FileOrganizerService

        organizer = FileOrganizerService()

        # Get rules to apply
        all_rules = organizer.get_rules(active_only=True)
        if self.rule_ids:
            rules = [(r, t) for r, t in all_rules if r.id in self.rule_ids]
        else:
            rules = all_rules

        if not rules:
            self._handleError("No active file organization rules found")
            return

        # Get books
        from cps import db
        calibre_session = db.calibre_db.session

        if self.book_ids:
            books = calibre_session.query(db.Books).filter(
                db.Books.id.in_(self.book_ids)
            ).all()
        else:
            books = calibre_session.query(db.Books).all()

        self._total = len(books)
        if self._total == 0:
            self._handleError("No books to organize")
            return

        # Process in batches
        batch_size = 50
        total_created = 0
        errors = []

        for batch_start in range(0, self._total, batch_size):
            # Check for cancellation
            if self.stat in (5,):  # STAT_CANCELLED
                log.info("File organization cancelled after %d books", self._processed)
                break

            batch_end = min(batch_start + batch_size, self._total)
            batch = books[batch_start:batch_end]

            for book in batch:
                matched = organizer._resolve_rule_conflicts(book.id, rules)

                for rule, tag_names in matched:
                    ok, link_path, err = organizer.create_link(
                        book, rule.target_directory, rule.link_type
                    )
                    if ok:
                        total_created += 1
                    else:
                        errors.append(
                            "Book {} ({}): {}".format(book.id, book.title, err)
                        )

                self._processed += 1
                self.progress = self._processed / self._total if self._total > 0 else 1.0

        if errors:
            log.warning("File organization completed with %d errors", len(errors))
            error_summary = "\n".join(errors[:50])  # Limit error log size
            if len(errors) > 50:
                error_summary += "\n... and {} more errors".format(len(errors) - 50)
            self._handleError(error_summary)
        else:
            self._handleSuccess()

        log.info(
            "File organization done: %d links created, %d errors",
            total_created, len(errors)
        )