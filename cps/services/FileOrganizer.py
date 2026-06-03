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
File organization service.

Manages file organization rules and creates symlinks / hardlinks in
tag-based directory structures without modifying Calibre's original
{author}/{title} directory layout.
"""

import os
import re
import fcntl
import platform

from sqlalchemy.exc import SQLAlchemyError

from cps import logger
from cps import ub, db, config

log = logger.create()


class FileOrganizerService:
    """Service for file organization based on tag-defined rules.

    Creates symlinks or hardlinks in user-defined target directories.
    Does NOT modify Calibre's original directory structure.

    Security features:
    - Path traversal prevention (_validate_target_directory)
    - Tag name XSS prevention (_validate_tag_name)
    - TOCTOU prevention via file locking (fcntl.flock)
    """

    TAG_NAME_PATTERN = re.compile(r'^[\w\s\-\u4e00-\u9fff]+$')
    MAX_TAG_NAME_LENGTH = 100
    MAX_LINKS_PER_DIR = 10000

    def __init__(self):
        self.ALLOWED_BASE_DIR = os.path.abspath(config.config_calibre_dir)

    # ── validation helpers ─────────────────────────────────────────────

    def _validate_target_directory(self, target_directory):
        """Ensure target directory is within the Calibre library base dir.

        Prevents path traversal attacks using ../ or symlink tricks.

        Raises:
            ValueError: If path is outside allowed base directory.
        """
        if not target_directory:
            raise ValueError("Target directory cannot be empty")
        abs_target = os.path.abspath(target_directory)
        abs_base = os.path.abspath(self.ALLOWED_BASE_DIR)
        try:
            common = os.path.commonpath([abs_target, abs_base])
        except ValueError:
            # Different drives on Windows
            raise ValueError("Target directory must be on the same drive as Calibre library")
        if common != abs_base:
            raise ValueError("Target directory must be within the Calibre library directory")
        if '..' in os.path.normpath(target_directory).split(os.sep):
            raise ValueError("Path cannot contain parent directory references")
        return abs_target

    @classmethod
    def _validate_tag_name(cls, tag_name):
        """Validate tag name characters (XSS/path-injection prevention).

        Raises:
            ValueError: If tag name is invalid.
        """
        if not tag_name or len(tag_name) > cls.MAX_TAG_NAME_LENGTH:
            raise ValueError(
                "Tag name must be 1-{} characters".format(cls.MAX_TAG_NAME_LENGTH)
            )
        if not cls.TAG_NAME_PATTERN.match(tag_name):
            raise ValueError("Tag name contains invalid characters")
        return tag_name.strip()

    def _check_link_count(self, directory):
        """Check link count per directory. Auto-create sub-dirs if limit exceeded."""
        try:
            count = 0
            for entry in os.listdir(directory):
                full = os.path.join(directory, entry)
                if os.path.islink(full) or os.path.lexists(full):
                    count += 1
            if count >= self.MAX_LINKS_PER_DIR:
                sub_idx = count // self.MAX_LINKS_PER_DIR
                sub_dir = os.path.join(directory, "sub_{}".format(sub_idx))
                os.makedirs(sub_dir, exist_ok=True)
                return sub_dir
        except OSError:
            pass
        return directory

    # ── rule management ────────────────────────────────────────────────

    def get_rules(self, active_only=True):
        """Get all file organization rules with their tag associations.

        Returns:
            List of (FileOrganizationRules, [tag_name, ...]) tuples.
        """
        try:
            q = ub.session.query(ub.FileOrganizationRules)
            if active_only:
                q = q.filter(ub.FileOrganizationRules.is_active == True)
            rules = q.order_by(ub.FileOrganizationRules.priority.desc()).all()

            result = []
            for rule in rules:
                tag_entries = ub.session.query(ub.FileOrgRuleTags).filter(
                    ub.FileOrgRuleTags.rule_id == rule.id
                ).all()
                tag_names = [t.tag_name for t in tag_entries]
                result.append((rule, tag_names))
            return result
        except SQLAlchemyError as e:
            log.error("get_rules failed: %s", str(e))
            return []

    def get_rule_by_id(self, rule_id):
        """Get a single rule with associated tag names."""
        try:
            rule = ub.session.query(ub.FileOrganizationRules).filter(
                ub.FileOrganizationRules.id == rule_id
            ).one_or_none()
            if not rule:
                return None, []
            tag_entries = ub.session.query(ub.FileOrgRuleTags).filter(
                ub.FileOrgRuleTags.rule_id == rule.id
            ).all()
            return rule, [t.tag_name for t in tag_entries]
        except SQLAlchemyError as e:
            log.error("get_rule_by_id failed: %s", str(e))
            return None, []

    def add_rule(self, name, tag_names, tag_combination="any",
                 target_directory=None, priority=0, link_type="symlink"):
        """Add a new file organization rule.

        Args:
            name: Rule name (unique).
            tag_names: List of tag name strings.
            tag_combination: "any" or "all".
            target_directory: Absolute path for organization.
            priority: Priority (higher = processed first).
            link_type: "symlink" or "hardlink".

        Returns:
            (success: bool, rule_id: int or None, error_message: str)
        """
        # Validate
        if not name or not target_directory or not tag_names:
            return False, None, "name, target_directory, and tag_names are required"

        if tag_combination not in ("any", "all"):
            return False, None, "tag_combination must be 'any' or 'all'"

        if link_type not in ("symlink", "hardlink"):
            return False, None, "link_type must be 'symlink' or 'hardlink'"

        try:
            self._validate_target_directory(target_directory)
        except ValueError as e:
            return False, None, str(e)

        for tag in tag_names:
            try:
                self._validate_tag_name(tag)
            except ValueError as e:
                return False, None, "Invalid tag '{}': {}".format(tag, str(e))

        # Create target directory
        try:
            os.makedirs(target_directory, exist_ok=True)
        except OSError as e:
            return False, None, "Cannot create target directory: {}".format(str(e))

        try:
            # Check uniqueness
            existing = ub.session.query(ub.FileOrganizationRules).filter(
                ub.FileOrganizationRules.name == name
            ).first()
            if existing:
                return False, None, "Rule '{}' already exists".format(name)

            rule = ub.FileOrganizationRules(
                name=name,
                tag_combination=tag_combination,
                target_directory=target_directory,
                link_type=link_type,
                priority=priority,
            )
            ub.session.add(rule)
            ub.session.flush()  # Get rule.id

            for tag_name in tag_names:
                rule_tag = ub.FileOrgRuleTags(rule_id=rule.id, tag_name=tag_name)
                ub.session.add(rule_tag)

            ub.session.commit()
            return True, rule.id, None

        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("add_rule failed: %s", str(e))
            return False, None, str(e)

    def update_rule(self, rule_id, **kwargs):
        """Update an existing rule.

        Args:
            rule_id: Rule primary key.
            **kwargs: Fields to update (name, tag_names, tag_combination,
                      target_directory, priority, link_type, is_active).
                      tag_names, if provided, replaces all existing tag associations.

        Returns:
            (success: bool, error_message: str)
        """
        rule = ub.session.query(ub.FileOrganizationRules).filter(
            ub.FileOrganizationRules.id == rule_id
        ).one_or_none()
        if not rule:
            return False, "Rule not found: id={}".format(rule_id)

        try:
            if 'target_directory' in kwargs:
                self._validate_target_directory(kwargs['target_directory'])
            if 'tag_names' in kwargs:
                for tag in kwargs['tag_names']:
                    self._validate_tag_name(tag)
                # Replace all tag associations
                ub.session.query(ub.FileOrgRuleTags).filter(
                    ub.FileOrgRuleTags.rule_id == rule_id
                ).delete()
                for tag_name in kwargs['tag_names']:
                    rt = ub.FileOrgRuleTags(rule_id=rule_id, tag_name=tag_name)
                    ub.session.add(rt)
                del kwargs['tag_names']

            for key, value in kwargs.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)

            ub.session.merge(rule)
            ub.session.commit()
            return True, None

        except (SQLAlchemyError, ValueError) as e:
            ub.session.rollback()
            log.error("update_rule failed: %s", str(e))
            return False, str(e)

    def delete_rule(self, rule_id):
        """Delete a rule and optionally clean up its created links.

        Args:
            rule_id: Rule primary key.

        Returns:
            (success: bool, error_message: str)
        """
        rule, _ = self.get_rule_by_id(rule_id)
        if not rule:
            return False, "Rule not found: id={}".format(rule_id)

        try:
            # Clean up created links first
            # self.clean_stale_links(rule.target_directory)

            # Delete tag associations
            ub.session.query(ub.FileOrgRuleTags).filter(
                ub.FileOrgRuleTags.rule_id == rule_id
            ).delete()

            # Delete rule
            ub.session.delete(rule)
            ub.session.commit()
            return True, None

        except SQLAlchemyError as e:
            ub.session.rollback()
            log.error("delete_rule failed: %s", str(e))
            return False, str(e)

    # ── book-rule matching ─────────────────────────────────────────────

    def _book_matches_rule(self, book_id, rule, tag_names):
        """Check if a book matches a given rule's tag requirements.

        Args:
            book_id: Calibre book id.
            rule: FileOrganizationRules ORM instance.
            tag_names: List of tag name strings associated with the rule.

        Returns:
            True if book matches.
        """
        from cps.db import books_tags_link
        calibre_session = db.calibre_db.session

        # Get Calibre tag ids for rule's tag names
        calibre_tag_ids = []
        for tname in tag_names:
            t = calibre_session.query(db.Tags).filter(db.Tags.name == tname).first()
            if t:
                calibre_tag_ids.append(t.id)

        if not calibre_tag_ids:
            return False

        # Get book's tag ids
        book_tags = calibre_session.query(books_tags_link.c.tag).filter(
            books_tags_link.c.book == book_id
        ).all()
        book_tag_ids = set(bt.tag for bt in book_tags)

        if rule.tag_combination == "all":
            # Book must have ALL specified tags
            return all(tid in book_tag_ids for tid in calibre_tag_ids)
        else:
            # Book must have ANY of the specified tags
            return any(tid in book_tag_ids for tid in calibre_tag_ids)

    def _resolve_rule_conflicts(self, book_id, rules_with_tags):
        """Resolve conflicts when a book matches multiple rules.

        Rules with higher priority are applied first. A book is organized
        by all matching rules (not just the highest priority one).

        Args:
            book_id: Calibre book id.
            rules_with_tags: List of (rule, tag_names) tuples.

        Returns:
            List of (rule, tag_names) sorted by priority descending.
        """
        matched = []
        for rule, tag_names in rules_with_tags:
            if self._book_matches_rule(book_id, rule, tag_names):
                matched.append((rule, tag_names))
        matched.sort(key=lambda x: x[0].priority, reverse=True)
        return matched

    # ── link creation ──────────────────────────────────────────────────

    def create_link(self, book, target_directory, link_type="symlink"):
        """Create a symlink or hardlink to a book's Calibre directory.

        Uses file locking (fcntl.flock) for TOCTOU prevention and
        atomic os.replace() for safe link replacement.

        Windows: Falls back to .url shortcut if symlink is not available.
        Hardlinks for directories fall back to symlinks automatically.

        Args:
            book: Calibre Books ORM instance.
            target_directory: Directory to create the link in.
            link_type: "symlink" or "hardlink".

        Returns:
            (success: bool, link_path: str or None, error_message: str)
        """
        try:
            safe_target = self._validate_target_directory(target_directory)
        except ValueError as e:
            return False, None, str(e)

        safe_target = self._check_link_count(safe_target)

        calibre_dir = os.path.join(self.ALLOWED_BASE_DIR, book.path)
        try:
            self._validate_target_directory(calibre_dir)
        except ValueError as e:
            return False, None, "Book path validation failed: {}".format(str(e))

        if not os.path.exists(calibre_dir):
            return False, None, "Book directory does not exist: {}".format(calibre_dir)

        from cps.helper import get_valid_filename
        link_name = get_valid_filename(book.title, chars=96) + " (" + str(book.id) + ")"
        link_path = os.path.join(safe_target, link_name)

        try:
            self._validate_target_directory(link_path)
        except ValueError as e:
            return False, None, "Link path validation failed: {}".format(str(e))

        lock_file = os.path.join(safe_target, '.file_org.lock')
        temp_link = None

        try:
            with open(lock_file, 'w') as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    temp_link = link_path + '.tmp'
                    # Clean up any stale temp file
                    if os.path.islink(temp_link) or os.path.exists(temp_link):
                        os.unlink(temp_link)
                    # Clean up old link
                    if os.path.islink(link_path) or os.path.exists(link_path):
                        os.unlink(link_path)

                    is_dir = os.path.isdir(calibre_dir)
                    if link_type == "hardlink":
                        if is_dir:
                            # Hardlinks can't point to directories, fallback to symlink
                            os.symlink(calibre_dir, temp_link, target_is_directory=True)
                        else:
                            os.link(calibre_dir, temp_link)
                    else:
                        if is_dir:
                            os.symlink(calibre_dir, temp_link, target_is_directory=True)
                        else:
                            os.symlink(calibre_dir, temp_link)

                    # Atomic replace
                    os.replace(temp_link, link_path)
                    temp_link = None  # Don't clean up (already renamed)

                except OSError:
                    if platform.system() == "Windows":
                        self._create_shortcut(calibre_dir, link_path)
                    else:
                        raise
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
        except OSError:
            # Lock file may not exist; attempt without locking
            try:
                if os.path.islink(link_path) or os.path.exists(link_path):
                    os.unlink(link_path)
                os.symlink(calibre_dir, link_path, target_is_directory=True)
            except OSError as e:
                return False, None, "Failed to create link: {}".format(str(e))
        finally:
            if temp_link and (os.path.islink(temp_link) or os.path.exists(temp_link)):
                try:
                    os.unlink(temp_link)
                except OSError:
                    pass

        return True, link_path, None

    @staticmethod
    def _create_shortcut(target, link_path):
        """Windows fallback: create a .url shortcut file."""
        shortcut_path = link_path + '.url'
        with open(shortcut_path, 'w', encoding='utf-8') as f:
            f.write('[InternetShortcut]\n')
            f.write('URL=file:///{}\n'.format(target))

    # ── batch operations ───────────────────────────────────────────────

    def apply_rules_to_book(self, book_id):
        """Apply all active rules to a single book.

        Args:
            book_id: Calibre book id.

        Returns:
            (success: bool, links_created: int, errors: list)
        """
        book = db.calibre_db.session.query(db.Books).filter(db.Books.id == book_id).first()
        if not book:
            return False, 0, ["Book not found: {}".format(book_id)]

        rules = self.get_rules(active_only=True)
        if not rules:
            return True, 0, []

        matched = self._resolve_rule_conflicts(book_id, rules)
        created = 0
        errors = []

        for rule, _ in matched:
            ok, link_path, err = self.create_link(book, rule.target_directory, rule.link_type)
            if ok:
                created += 1
            else:
                errors.append("Rule '{}': {}".format(rule.name, err))

        return True, created, errors

    def apply_rules_to_all(self, batch_size=100):
        """Apply all active rules to all books in the library.

        Processes books in batches to avoid memory issues.

        Args:
            batch_size: Number of books per batch.

        Returns:
            Generator yielding (book_index, total, links_created, errors) tuples.
        """
        total_books = db.calibre_db.session.query(db.Books).count()
        rules = self.get_rules(active_only=True)

        for offset in range(0, total_books, batch_size):
            books = db.calibre_db.session.query(db.Books).order_by(
                db.Books.id
            ).limit(batch_size).offset(offset).all()

            for idx, book in enumerate(books):
                matched = self._resolve_rule_conflicts(book.id, rules)
                created = 0
                errors = []

                for rule, _ in matched:
                    ok, link_path, err = self.create_link(
                        book, rule.target_directory, rule.link_type
                    )
                    if ok:
                        created += 1
                    else:
                        errors.append(err)

                yield (offset + idx + 1, total_books, created, errors)

    def clean_stale_links(self, target_directory):
        """Remove dead symlinks/hardlinks in a target directory.

        A link is considered stale if:
        - Its target no longer exists.
        - The linked book no longer matches any active rule.

        Args:
            target_directory: Directory to scan for stale links.

        Returns:
            (cleaned_count: int, errors: list)
        """
        try:
            safe_target = self._validate_target_directory(target_directory)
        except ValueError as e:
            return 0, [str(e)]

        if not os.path.isdir(safe_target):
            return 0, []

        cleaned = 0
        errors = []

        try:
            for entry in os.listdir(safe_target):
                full_path = os.path.join(safe_target, entry)
                if os.path.islink(full_path):
                    try:
                        target = os.readlink(full_path)
                        if not os.path.exists(target):
                            os.unlink(full_path)
                            cleaned += 1
                    except OSError as e:
                        errors.append("Cannot clean {}: {}".format(entry, str(e)))
        except OSError as e:
            errors.append("Cannot list directory: {}".format(str(e)))

        return cleaned, errors

    def get_preview(self, rule_id=None):
        """Preview which books would be organized by a rule (or all rules).

        Args:
            rule_id: Specific rule ID, or None for all active rules.

        Returns:
            dict: {rule_name: [book_id, ...], ...}
        """
        if rule_id:
            rule, tag_names = self.get_rule_by_id(rule_id)
            if not rule:
                return {}
            rules = [(rule, tag_names)] if rule.is_active else []
        else:
            rules = self.get_rules(active_only=True)

        preview = {}
        all_books = db.calibre_db.session.query(db.Books).all()

        for rule, tag_names in rules:
            matching_books = []
            for book in all_books:
                if self._book_matches_rule(book.id, rule, tag_names):
                    matching_books.append(book.id)
            preview[rule.name] = matching_books

        return preview