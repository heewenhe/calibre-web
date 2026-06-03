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
Tag library management service.

Provides CRUD operations for tags in both Calibre's Tags table (db.py)
and the TagLibrary metadata extension table (ub.py). Handles cross-database
synchronization between the two independent SQLite databases.
"""

import time

from sqlalchemy.exc import SQLAlchemyError

from cps import logger
from cps import ub, db

log = logger.create()


class TagLibraryService:
    """Service for managing tag library metadata.

    Maintains synchronization between Calibre's Tags table (db.py) and
    the TagLibrary extension table (ub.py). Because these two tables live
    in separate SQLite databases, all cross-database operations use an
    application-layer transaction strategy:

    1. Write to Calibre DB first
    2. Write to User DB (TagLibrary)
    3. On User DB failure, rollback Calibre DB manually
    """

    def __init__(self):
        self._tag_cache = {}  # {tag_name: calibre_tag_id}
        self._cache_ttl = 300  # seconds
        self._cache_timestamp = 0

    # ── cache helpers ──────────────────────────────────────────────────

    def _refresh_cache_if_needed(self):
        """Refresh cached Calibre tag name -> id mapping when expired."""
        if time.time() - self._cache_timestamp > self._cache_ttl:
            self._tag_cache = {}
            self._cache_timestamp = time.time()

    def _get_or_fill_cache(self, calibre_session):
        """Ensure the tag name cache is populated from Calibre."""
        self._refresh_cache_if_needed()
        if not self._tag_cache:
            from cps.db import Tags
            all_tags = calibre_session.query(Tags).all()
            for tag in all_tags:
                self._tag_cache[tag.name.lower()] = tag.id
        return self._tag_cache

    def _invalidate_cache(self):
        """Force cache refresh on next access."""
        self._cache_timestamp = 0

    # ── cross-DB transaction helpers ───────────────────────────────────

    @staticmethod
    def _safe_commit(session, label=""):
        """Commit a session, rolling back on error."""
        try:
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            log.error("Commit failed [%s]: %s", label, str(e))
            return False

    # ── query methods ─────────────────────────────────────────────────

    def get_all_tags(self, category=None, active_only=True):
        """Get all tags from TagLibrary table.

        Args:
            category: Optional filter by category name.
            active_only: If True, return only active tags.

        Returns:
            List of TagLibrary ORM objects.
        """
        try:
            q = ub.session.query(ub.TagLibrary)
            if active_only:
                q = q.filter(ub.TagLibrary.is_active == True)
            if category is not None:
                q = q.filter(ub.TagLibrary.category == category)
            return q.order_by(ub.TagLibrary.name).all()
        except SQLAlchemyError as e:
            log.error("Failed to get tags: %s", str(e))
            return []

    def get_tag_by_id(self, tag_id):
        """Get a single TagLibrary record by id."""
        try:
            return ub.session.query(ub.TagLibrary).filter(
                ub.TagLibrary.id == tag_id
            ).one_or_none()
        except SQLAlchemyError as e:
            log.error("Failed to get tag %d: %s", tag_id, str(e))
            return None

    def get_calibre_tags(self):
        """Get all tags from Calibre's Tags table.

        Returns:
            List of Tags ORM objects from db.py.
        """
        try:
            return db.calibre_db.session.query(db.Tags).order_by(db.Tags.name).all()
        except SQLAlchemyError as e:
            log.error("Failed to get Calibre tags: %s", str(e))
            return []

    def get_tag_usage_count(self, calibre_tag_id):
        """Count how many books are linked to a given Calibre tag."""
        try:
            from cps.db import books_tags_link
            from sqlalchemy import func
            count = db.calibre_db.session.query(func.count(books_tags_link.c.book)).filter(
                books_tags_link.c.tag == calibre_tag_id
            ).scalar()
            return count or 0
        except SQLAlchemyError as e:
            log.error("Failed to get tag usage count: %s", str(e))
            return 0

    def search_tags(self, keyword, category=None):
        """Search tags by name keyword in TagLibrary."""
        try:
            q = ub.session.query(ub.TagLibrary).filter(
                ub.TagLibrary.name.ilike("%" + keyword + "%")
            )
            if category:
                q = q.filter(ub.TagLibrary.category == category)
            return q.order_by(ub.TagLibrary.name).all()
        except SQLAlchemyError as e:
            log.error("Failed to search tags: %s", str(e))
            return []

    # ── CRUD operations ────────────────────────────────────────────────

    def add_tag(self, name, category="", description=""):
        """Add a tag to both Calibre Tags and TagLibrary.

        Transaction strategy:
        1. Check if tag already exists in Calibre Tags (by name).
        2. If not, create it in Calibre Tags.
        3. Create TagLibrary record referencing calibre_tag_id.
        4. If step 3 fails, rollback step 2.

        Args:
            name: Tag name.
            category: Optional category.
            description: Optional description.

        Returns:
            (success: bool, tag_library_id: int or None, error_message: str)
        """
        name = name.strip()
        if not name:
            return False, None, "Tag name cannot be empty"

        # Check duplicate in TagLibrary
        existing = ub.session.query(ub.TagLibrary).filter(
            ub.TagLibrary.name == name
        ).first()
        if existing:
            return False, None, "Tag '{}' already exists in TagLibrary".format(name)

        calibre_session = db.calibre_db.session
        calibre_tag_id = None
        calibre_tag_created = False  # Track if we just created the Calibre tag

        try:
            # Step 1: Check/create in Calibre Tags
            calibre_tag = calibre_session.query(db.Tags).filter(
                db.Tags.name == name
            ).first()
            if calibre_tag:
                calibre_tag_id = calibre_tag.id
            else:
                calibre_tag = db.Tags(name)
                calibre_session.add(calibre_tag)
                calibre_session.flush()
                calibre_tag_id = calibre_tag.id
                calibre_tag_created = True

            # Step 2: Create TagLibrary record
            tag_lib = ub.TagLibrary(
                name=name,
                calibre_tag_id=calibre_tag_id,
                category=category,
                description=description,
                usage_count=self.get_tag_usage_count(calibre_tag_id),
            )
            ub.session.add(tag_lib)
            if not self._safe_commit(ub.session, "add_tag"):
                # Rollback Calibre DB only if we just created the tag
                if calibre_tag_created:
                    calibre_session.rollback()
                return False, None, "Failed to save TagLibrary record"

            self._safe_commit(calibre_session, "add_tag_calibre")
            self._invalidate_cache()
            return True, tag_lib.id, None

        except SQLAlchemyError as e:
            calibre_session.rollback()
            ub.session.rollback()
            log.error("add_tag failed: %s", str(e))
            return False, None, str(e)

    def update_tag(self, tag_id, name=None, category=None, description=None):
        """Update a tag in both Calibre Tags and TagLibrary.

        When the tag name changes, FileOrgRuleTags.tag_name is also updated
        to maintain string-based association consistency.

        Transaction strategy:
        1. Update Calibre Tags.name (if name changed).
        2. Update FileOrgRuleTags.tag_name (if name changed).
        3. Update TagLibrary record.

        Args:
            tag_id: TagLibrary primary key.
            name: New tag name (optional).
            category: New category (optional).
            description: New description (optional).

        Returns:
            (success: bool, error_message: str)
        """
        tag_lib = self.get_tag_by_id(tag_id)
        if not tag_lib:
            return False, "Tag not found: id={}".format(tag_id)

        old_name = tag_lib.name
        new_name = name.strip() if name else None

        if new_name and new_name != old_name:
            # Check duplicate
            dup = ub.session.query(ub.TagLibrary).filter(
                ub.TagLibrary.name == new_name,
                ub.TagLibrary.id != tag_id
            ).first()
            if dup:
                return False, "Tag name '{}' already exists".format(new_name)

        calibre_session = db.calibre_db.session

        try:
            # Step 1: Update Calibre Tags if name changed and calibre_tag_id exists
            if new_name and tag_lib.calibre_tag_id:
                calibre_tag = calibre_session.query(db.Tags).filter(
                    db.Tags.id == tag_lib.calibre_tag_id
                ).first()
                if calibre_tag:
                    calibre_tag.name = new_name
                    self._safe_commit(calibre_session, "update_tag_calibre")

            # Step 2: Update FileOrgRuleTags.tag_name
            if new_name and new_name != old_name:
                ub.session.query(ub.FileOrgRuleTags).filter(
                    ub.FileOrgRuleTags.tag_name == old_name
                ).update({"tag_name": new_name}, synchronize_session=False)

            # Step 3: Update TagLibrary
            if new_name:
                tag_lib.name = new_name
            if category is not None:
                tag_lib.category = category
            if description is not None:
                tag_lib.description = description

            ub.session.merge(tag_lib)
            if not self._safe_commit(ub.session, "update_tag"):
                return False, "Failed to save TagLibrary update"

            self._invalidate_cache()
            return True, None

        except SQLAlchemyError as e:
            calibre_session.rollback()
            ub.session.rollback()
            log.error("update_tag failed: %s", str(e))
            return False, str(e)

    def delete_tag(self, tag_id):
        """Delete a tag from both Calibre Tags and TagLibrary.

        Transaction strategy:
        1. Remove books_tags_link associations in Calibre DB.
        2. Delete Calibre Tags record.
        3. Delete TagLibrary record.
        4. If any step fails, attempt rollback.

        Args:
            tag_id: TagLibrary primary key.

        Returns:
            (success: bool, error_message: str)
        """
        tag_lib = self.get_tag_by_id(tag_id)
        if not tag_lib:
            return False, "Tag not found: id={}".format(tag_id)

        calibre_session = db.calibre_db.session
        calibre_tag_id = tag_lib.calibre_tag_id

        try:
            # Step 1: Remove books_tags_link associations
            if calibre_tag_id:
                from cps.db import books_tags_link
                calibre_session.execute(
                    books_tags_link.delete().where(
                        books_tags_link.c.tag == calibre_tag_id
                    )
                )

                # Step 2: Delete Calibre Tags record
                calibre_session.query(db.Tags).filter(
                    db.Tags.id == calibre_tag_id
                ).delete()
                self._safe_commit(calibre_session, "delete_tag_calibre")

            # Step 3: Clean up FileOrgRuleTags references
            ub.session.query(ub.FileOrgRuleTags).filter(
                ub.FileOrgRuleTags.tag_name == tag_lib.name
            ).delete()

            # Step 4: Delete TagLibrary
            ub.session.delete(tag_lib)
            if not self._safe_commit(ub.session, "delete_tag"):
                return False, "Failed to delete TagLibrary record"

            self._invalidate_cache()
            return True, None

        except SQLAlchemyError as e:
            calibre_session.rollback()
            ub.session.rollback()
            log.error("delete_tag failed: %s", str(e))
            return False, str(e)

    def merge_tags(self, source_tag_ids, target_tag_name):
        """Merge one or more source tags into a target tag.

        Updates books_tags_link in Calibre DB to point from source tags
        to the target tag. Uses batch processing (500 books per batch).

        Args:
            source_tag_ids: List of TagLibrary ids to merge FROM.
            target_tag_name: TagLibrary id to merge INTO.

        Returns:
            (success: bool, merged_count: int, error_message: str)
        """
        target_lib = ub.session.query(ub.TagLibrary).filter(
            ub.TagLibrary.name == target_tag_name
        ).first()
        if not target_lib or not target_lib.calibre_tag_id:
            return False, 0, "Target tag '{}' not found or not synced".format(target_tag_name)

        calibre_session = db.calibre_db.session
        target_calibre_id = target_lib.calibre_tag_id
        total_merged = 0

        try:
            from cps.db import books_tags_link
            for src_id in source_tag_ids:
                src_lib = self.get_tag_by_id(src_id)
                if not src_lib or not src_lib.calibre_tag_id:
                    continue
                if src_lib.calibre_tag_id == target_calibre_id:
                    continue

                # Batch update: move all books_tags_link from source to target
                batch_size = 500
                while True:
                    links = calibre_session.query(books_tags_link).filter(
                        books_tags_link.c.tag == src_lib.calibre_tag_id
                    ).limit(batch_size).all()

                    if not links:
                        break

                    for link in links:
                        # Check if target link already exists
                        exists_q = calibre_session.query(books_tags_link).filter(
                            books_tags_link.c.book == link.book,
                            books_tags_link.c.tag == target_calibre_id
                        ).first()
                        if not exists_q:
                            calibre_session.execute(
                                books_tags_link.update().where(
                                    books_tags_link.c.book == link.book,
                                    books_tags_link.c.tag == src_lib.calibre_tag_id
                                ).values(tag=target_calibre_id)
                            )
                            total_merged += 1
                        else:
                            # Already linked to target, just remove source
                            calibre_session.execute(
                                books_tags_link.delete().where(
                                    books_tags_link.c.book == link.book,
                                    books_tags_link.c.tag == src_lib.calibre_tag_id
                                )
                            )

                    self._safe_commit(calibre_session, "merge_tags_batch")

                # Delete the old Calibre tag
                calibre_session.query(db.Tags).filter(
                    db.Tags.id == src_lib.calibre_tag_id
                ).delete()

                # Update TagLibrary: mark source as inactive
                src_lib.is_active = False
                src_lib.calibre_tag_id = None
                ub.session.merge(src_lib)

            self._safe_commit(calibre_session, "merge_tags_final")
            self._safe_commit(ub.session, "merge_tags_ub")

            # Update usage count
            target_lib.usage_count = self.get_tag_usage_count(target_calibre_id)
            ub.session.merge(target_lib)
            self._safe_commit(ub.session, "merge_tags_usage")

            self._invalidate_cache()
            return True, total_merged, None

        except SQLAlchemyError as e:
            calibre_session.rollback()
            ub.session.rollback()
            log.error("merge_tags failed: %s", str(e))
            return False, total_merged, str(e)

    def categorize_tags(self, tag_ids, category):
        """Batch-set category for multiple tags.

        Args:
            tag_ids: List of TagLibrary ids.
            category: Category name to assign.

        Returns:
            (success: bool, updated_count: int, error_message: str)
        """
        try:
            updated = ub.session.query(ub.TagLibrary).filter(
                ub.TagLibrary.id.in_(tag_ids)
            ).update(
                {"category": category},
                synchronize_session=False
            )
            self._safe_commit(ub.session, "categorize_tags")
            return True, updated, None
        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("categorize_tags failed: %s", str(e))
            return False, 0, str(e)

    # ── synchronization and consistency ────────────────────────────────

    def sync_from_calibre(self):
        """Sync all Calibre Tags into TagLibrary (incremental).

        For each Calibre Tags record that does not exist in TagLibrary,
        create a corresponding TagLibrary entry.

        Returns:
            (success: bool, added_count: int, error_message: str)
        """
        try:
            calibre_tags = self.get_calibre_tags()
            existing_names = set()
            existing = ub.session.query(ub.TagLibrary.name, ub.TagLibrary.calibre_tag_id).all()
            existing_map = {e.name: e for e in existing}
            existing_names = set(existing_map.keys())

            added = 0
            for ctag in calibre_tags:
                if ctag.name not in existing_names:
                    tag_lib = ub.TagLibrary(
                        name=ctag.name,
                        calibre_tag_id=ctag.id,
                        usage_count=self.get_tag_usage_count(ctag.id),
                    )
                    ub.session.add(tag_lib)
                    added += 1
                else:
                    # Update calibre_tag_id if missing
                    if existing_map[ctag.name].calibre_tag_id is None:
                        update_q = ub.session.query(ub.TagLibrary).filter(
                            ub.TagLibrary.name == ctag.name
                        )
                        update_q.update({"calibre_tag_id": ctag.id})

            self._safe_commit(ub.session, "sync_from_calibre")
            self._invalidate_cache()
            return True, added, None

        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("sync_from_calibre failed: %s", str(e))
            return False, 0, str(e)

    def sync_consistency_check(self):
        """Check consistency between TagLibrary and Calibre Tags.

        Returns a dict with three lists:
        - orphaned_ids: TagLibrary entries whose calibre_tag_id doesn't exist in Calibre.
        - missing_calibre_tags: Calibre Tags entries not present in TagLibrary.
        - name_mismatch: Entries where names don't match.

        Returns:
            dict with keys: orphaned_ids, missing_calibre_tags, name_mismatch
        """
        result = {
            "orphaned_ids": [],
            "missing_calibre_tags": [],
            "name_mismatch": [],
        }

        try:
            # Build Calibre tag id->name map
            calibre_tags = self.get_calibre_tags()
            calibre_map = {t.id: t.name for t in calibre_tags}
            calibre_name_set = set(calibre_map.values())

            tag_libs = self.get_all_tags(active_only=False)
            for tl in tag_libs:
                if tl.calibre_tag_id is not None:
                    if tl.calibre_tag_id not in calibre_map:
                        result["orphaned_ids"].append({
                            "tag_library_id": tl.id,
                            "name": tl.name,
                            "calibre_tag_id": tl.calibre_tag_id,
                        })
                    elif calibre_map[tl.calibre_tag_id] != tl.name:
                        result["name_mismatch"].append({
                            "tag_library_id": tl.id,
                            "tag_library_name": tl.name,
                            "calibre_name": calibre_map[tl.calibre_tag_id],
                        })

            for ctag in calibre_tags:
                exists = ub.session.query(ub.TagLibrary).filter(
                    ub.TagLibrary.name == ctag.name
                ).first()
                if not exists:
                    result["missing_calibre_tags"].append({
                        "calibre_tag_id": ctag.id,
                        "name": ctag.name,
                    })

            return result

        except SQLAlchemyError as e:
            log.error("sync_consistency_check failed: %s", str(e))
            return result